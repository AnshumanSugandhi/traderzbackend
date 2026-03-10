import sys
import os
import csv
import json
import requests
import gc
import random
import re
from io import BytesIO
import pytesseract
from PIL import Image
from rest_framework.decorators import api_view
from rest_framework.response import Response
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

# ==========================================
# 1. OS & TESSERACT SETUP
# ==========================================
if sys.platform == 'win32':
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

AVAILABLE_KEYS = []
for i in range(1, 10):
    key = os.getenv(f"GEMINI_API_KEY_{i}")
    if key: AVAILABLE_KEYS.append(key)

if not AVAILABLE_KEYS and os.getenv("GEMINI_API_KEY"):
    AVAILABLE_KEYS.append(os.getenv("GEMINI_API_KEY"))

# ==========================================
# 2. LOGIN ENDPOINT
# ==========================================
EMPLOYEE_CREDENTIALS = {"EMP001": "pass123", "EMP002": "bot456", "admin": "12345"}

@api_view(['POST'])
def verify_login(request):
    emp_id = request.data.get('emp_id', '').strip()
    emp_pass = request.data.get('emp_pass', '').strip()
    if emp_id in EMPLOYEE_CREDENTIALS and EMPLOYEE_CREDENTIALS[emp_id] == emp_pass:
        return Response({"status": "success", "token": f"verified_token_{emp_id}"})
    return Response({"status": "error", "message": "Invalid ID or Password"}, status=401)

# ==========================================
# 3. CSV MAPPERS (UPGRADED FOR NEW SYNONYM SOP)
# ==========================================
def match_category_from_csv(text, company_name, ai_niche):
    text_lower = (str(text) + " " + str(company_name) + " " + str(ai_niche)).lower()
    ai_niche_lower = str(ai_niche or '').strip().lower()
    
    result = {
        "business_category": "Service Provider", 
        "business_sub_category": "", 
        "business_small_category": str(ai_niche or ''), 
        "category_not_in_list": False,
        "remarks": ""
    }
    
    # Safely resolve path regardless of where manage.py is run
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(base_dir, 'category_master.csv')
    if not os.path.exists(csv_path):
        csv_path = os.path.join(os.path.dirname(__file__), 'category_master.csv')
    
    if not os.path.exists(csv_path):
        result["category_not_in_list"] = True
        result["remarks"] = f"CNIL selected. Business type: {ai_niche}. Reason: category_master.csv missing."
        return result
        
    max_score = 0
    best_match = None
    is_exact = False
    
    try:
        with open(csv_path, mode='r', encoding='utf-8-sig', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                cat, sub_cat, small_cat = "", "", ""
                synonyms = []
                
                # Robust Header Parsing (Fixes the "Sub Category  on DMS" double-space bug)
                for key, val in row.items():
                    if not key or not val: continue
                    k_lower = key.lower().strip()
                    v_clean = str(val).strip()
                    
                    if k_lower in ['category on dms', 'category']:
                        cat = v_clean
                    elif 'sub category' in k_lower:
                        sub_cat = v_clean
                    elif 'small category' in k_lower:
                        small_cat = v_clean
                    elif 'synonym' in k_lower:
                        synonyms.append(v_clean.lower())
                
                if not small_cat: continue
                
                score = 0
                match_type = "none"
                all_targets = [small_cat.lower()] + synonyms
                
                # 1. SOP RULE 1: Exact Match Check
                if ai_niche_lower and ai_niche_lower in all_targets:
                    score = 100
                    match_type = "exact"
                else:
                    # 2. SOP RULE 2: Closest Match Check (Word Overlap)
                    niche_words = set(ai_niche_lower.split()) if ai_niche_lower else set()
                    target_words = set(small_cat.lower().replace('-', ' ').split())
                    
                    if niche_words and len(niche_words.intersection(target_words)) >= max(1, len(niche_words) - 1):
                        score = 80
                        match_type = "closest"
                    else:
                        # 3. Fallback: Scan entire website text for the category/synonyms
                        if small_cat.lower() in text_lower:
                            score = 60
                            match_type = "closest"
                        for syn in synonyms:
                            if syn and syn in text_lower:
                                score = max(score, 50)
                                match_type = "closest"
                
                if score > max_score:
                    max_score = score
                    best_match = (cat, sub_cat, small_cat)
                    is_exact = (match_type == "exact")
                    
        # Apply SOP Remarks Logic based on Score
        if max_score >= 50 and best_match:
            result["business_category"] = best_match[0] or "Service Provider"
            result["business_sub_category"] = best_match[1]
            result["business_small_category"] = best_match[2]
            
            if is_exact:
                result["remarks"] = "" 
            else:
                result["remarks"] = f"Closest match selected. Exact service: {ai_niche}"
        else:
            result["category_not_in_list"] = True
            result["business_category"] = "Service Provider"
            result["remarks"] = f"CNIL selected. Business type: {ai_niche}. Reason: No relevant category or synonym closely matched."
            
    except Exception as e:
        print("Category Match Error:", str(e))
        result["category_not_in_list"] = True
        result["remarks"] = f"CNIL selected. Business type: {ai_niche}."
        
    return result

# --- UPGRADED DMS LOCATOR: STATE + CITY COMBINATION MATCH ---
def normalize_location_from_dms(state_raw, city_raw):
    state_clean = str(state_raw or '').strip().lower()
    city_clean = str(city_raw or '').strip().lower()
    
    if city_clean in ["n.a.", "none", "null"]: city_clean = ""
    if state_clean in ["n.a.", "none", "null"]: state_clean = ""

    if not city_clean and not state_clean:
        return None

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(base_dir, 'dms_master.csv')
    if not os.path.exists(csv_path):
        csv_path = os.path.join(os.path.dirname(__file__), 'dms_master.csv')
    if not os.path.exists(csv_path):
        return None

    try:
        with open(csv_path, mode='r', encoding='utf-8-sig', errors='ignore') as f:
            reader = list(csv.DictReader(f)) 
            
            # PRIORITY 1: Match BOTH State and City on the same row
            if state_clean and city_clean:
                for row in reader:
                    r_state = (row.get('State') or row.get('state_name') or '').strip()
                    r_city = (row.get('City') or row.get('District') or row.get('city_name') or '').strip()
                    
                    if r_state.lower() == state_clean and r_city.lower() == city_clean:
                        return {
                            "state": r_state, 
                            "city": r_city, 
                            "pincode": (row.get('Pincode') or row.get('Pin') or row.get('pincode') or '').strip()
                        }
            
            # PRIORITY 2: Match City Only (Fallback if state was extracted poorly)
            if city_clean:
                for row in reader:
                    r_city = (row.get('City') or row.get('District') or row.get('city_name') or '').strip()
                    if r_city.lower() == city_clean:
                        return {
                            "state": (row.get('State') or row.get('state_name') or '').strip(), 
                            "city": r_city, 
                            "pincode": (row.get('Pincode') or row.get('Pin') or row.get('pincode') or '').strip()
                        }
    except Exception as e:
        pass
    return None

# ==========================================
# 4. MAIN API ENDPOINT
# ==========================================
@api_view(['POST'])
def analyze_website(request):
    try:
        data = request.data
        raw_text = data.get('text', '')
        raw_title = data.get('title', 'N.A.')
        target_url = data.get('url', '')
        social_links = data.get('socials', [])
        image_urls = data.get('images', []) 
        
        ocr_text = ""
        for img_url in image_urls[:1]: 
            try:
                response = requests.get(img_url, timeout=5)
                img = Image.open(BytesIO(response.content))
                if img.width > 800: img = img.resize((800, int(img.height * (800/img.width))), Image.Resampling.LANCZOS)
                elif img.width < 400: img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
                img_gray = img.convert('L') 
                text_normal = pytesseract.image_to_string(img_gray, config=r'--oem 3 --psm 11')
                if len(text_normal.strip()) > 5: ocr_text += f"\n {text_normal} \n"
                img.close(); img_gray.close(); del response, img, img_gray, text_normal; gc.collect() 
            except: continue
        
        combined_text = raw_title + "\n" + raw_text + "\n" + str(social_links) + "\n" + ocr_text

        if not AVAILABLE_KEYS: return Response({"error": "No Gemini API keys configured!"}, status=500)
        selected_key = random.choice(AVAILABLE_KEYS)
        genai.configure(api_key=selected_key)

        system_prompt = """
        You are an expert data extraction AI. Extract the business details into JSON.
        STRICT RULES:
        1. COMPANY NAME: Extract brand name. STRIP OUT "by [Name]" or "Powered by".
        2. LOCALITY: Extract neighborhood, street, or landmark. If none, output City name.
        3. PINCODE: Extract 6-digit PIN. If missing, leave empty string.
        4. OWNER NAME: Look for 'Director', 'Founder', or 'Proprietor'.
        5. ALTERNATE PHONE: Use 'N.A.' if none.
        6. JSON FORMAT ONLY. No markdown tags.
        7. LOCATION DETERMINATION: If the extracted city/state is inside Maharashtra, output is_maharashtra: true.
        8. AI NICHES: Identify up to 3 distinct core business products or services offered (e.g., ["Hotel", "Restaurant", "Event Space"]). This MUST be a list of strings!
        
        EXPECTED KEYS: { "company_name": "", "owner_name": "", "primary_phone": "", "alternate_phone": "", "email_1": "", "email_2": "", "full_address": "", "locality": "", "state_name": "", "city_name": "", "pincode_value": "", "ai_niches": [], "is_maharashtra": true }
        """

        model = genai.GenerativeModel(model_name="gemini-2.5-flash", generation_config={"response_mime_type": "application/json"})
        ai_response = model.generate_content(system_prompt + "\n\n--- TEXT ---\n" + combined_text[:25000])
        
        try:
            res_text = ai_response.text.strip()
            if res_text.startswith("```"):
                res_text = re.sub(r'^```[a-z]*\n|```$', '', res_text, flags=re.MULTILINE)
            extracted_data = json.loads(res_text)
        except:
            return Response({"error": "AI generated invalid JSON"}, status=500)

        # --- 1. Clean Company Name ---
        company_name = str(extracted_data.get("company_name") or "N.A.").strip()
        if " by " in company_name.lower():
            company_name = company_name[:company_name.lower().rfind(" by ")].strip()

        # --- 2. Normalize Location via DMS Master (State + City priority) ---
        raw_pin = extracted_data.get("pincode_value") or ""
        raw_city = extracted_data.get("city_name") or ""
        raw_state = extracted_data.get("state_name") or ""
        
        verified_loc = normalize_location_from_dms(raw_state, raw_city)
        
        if verified_loc:
            state_name = verified_loc["state"]
            city_name = verified_loc["city"]
            pincode_value = verified_loc["pincode"] # Overwrites with correct DMS pincode!
        else:
            state_name = str(raw_state)
            city_name = str(raw_city)
            pincode_value = re.sub(r'\D', '', str(raw_pin))[:6]

        if str(city_name).upper() == "N.A.": city_name = ""
        if str(state_name).upper() == "N.A.": state_name = ""

        raw_address = str(extracted_data.get("full_address") or "").strip()
        if raw_address.lower() in ["n.a.", "none", "null"]: raw_address = ""
        
        addr_parts = []
        if company_name and company_name.upper() != "N.A.": addr_parts.append(company_name)
        if raw_address:
            if company_name.lower() not in raw_address.lower(): addr_parts.append(raw_address)
        else:
            if not addr_parts: addr_parts.append(city_name)
            
        final_address = ", ".join(addr_parts)
        if city_name and city_name.lower() not in final_address.lower(): final_address += f", {city_name}"
        if pincode_value and pincode_value not in final_address: final_address += f" - {pincode_value}"

        full_address = final_address.strip(" ,-")
        locality = str(extracted_data.get("locality") or "").strip()
        if locality.lower() in ["n.a.", "none", "null"]: locality = city_name

        is_maharashtra = bool(extracted_data.get("is_maharashtra", True))
        safe_state = str(state_name or "").lower()
        if safe_state and "maharashtra" not in safe_state:
            is_maharashtra = False
        elif safe_state and "maharashtra" in safe_state:
            is_maharashtra = True

        response_data = {
            "company_name": company_name[:150],
            "owner_name": str(extracted_data.get("owner_name") or "N.A."),
            "primary_phone": str(extracted_data.get("primary_phone") or "N.A."),
            "alternate_phone": str(extracted_data.get("alternate_phone") or "N.A."),
            "email_1": str(extracted_data.get("email_1") or ""),
            "email_2": str(extracted_data.get("email_2") or "N.A."),
            "full_address": full_address,
            "locality": locality,
            "state_name": state_name,
            "city_name": city_name,
            "pincode_value": pincode_value,
            "is_maharashtra": is_maharashtra,
            "ocr_text": ocr_text
        }

        # -----------------------------------------------------
        # MULTI-CATEGORY LOOP ENGINE WITH FAILSAFE
        # -----------------------------------------------------
        ai_niches = extracted_data.get("ai_niches", [])
        if not isinstance(ai_niches, list):
            ai_niches = [ai_niches] if ai_niches else []
            
        if not ai_niches and extracted_data.get("ai_niche"):
            ai_niches = [extracted_data.get("ai_niche")]
            
        if not ai_niches:
            ai_niches = ["Service Provider"]

        categories_list = []
        for niche in ai_niches:
            niche_clean = str(niche).strip()
            if niche_clean and niche_clean.lower() != "n.a.":
                cat_data = match_category_from_csv(combined_text, company_name, niche_clean)
                categories_list.append(cat_data)
                
        response_data["categories_list"] = categories_list
        
        # FAILSAFE: Always append the first matched category directly to the root response 
        # so the Chrome Extension always has something to click even if the loop fails!
        if categories_list:
            response_data.update(categories_list[0])

        return Response(response_data)

    except Exception as e:
        print(f"[FATAL SERVER ERROR] {str(e)}")
        return Response({"error": str(e)}, status=500)