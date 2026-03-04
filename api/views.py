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
# 3. CSV MAPPERS (CATEGORY & LOCATION)
# ==========================================
def match_category_from_csv(text, company_name, ai_niche):
    # Safely handle None types by casting to string
    text_lower = (str(text) + " " + str(company_name) + " " + str(ai_niche)).lower()
    result = {"business_category": "Service Provider", "business_sub_category": "", "business_small_category": str(ai_niche), "category_not_in_list": False}
    
    csv_path = os.path.join(os.path.dirname(__file__), 'category_master.csv')
    if not os.path.exists(csv_path):
        result["category_not_in_list"] = True
        return result
        
    max_score = 0
    try:
        with open(csv_path, mode='r', encoding='utf-8-sig', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                cat = row.get('Category', '').strip()
                sub_cat = row.get('Sub Category', '').strip()
                small_cat = row.get('Small Category', '').strip()
                
                score = 0
                if small_cat and small_cat.lower() in text_lower: score += 20
                if sub_cat and sub_cat.lower() in text_lower: score += 10
                if ai_niche and str(ai_niche).lower() in small_cat.lower(): score += 15
                            
                if score > max_score and score > 0:
                    max_score = score
                    result.update({"business_category": cat if cat else "Service Provider", "business_sub_category": sub_cat, "business_small_category": small_cat})
                    
        if max_score < 15:
            result.update({"category_not_in_list": True, "business_category": "Service Provider"})
    except:
        result["category_not_in_list"] = True
    return result

def normalize_location_from_dms(pincode_raw, city_raw):
    pincode_clean = re.sub(r'\D', '', str(pincode_raw or ''))[:6]
    city_clean = str(city_raw or '').strip().lower()
    if city_clean == "n.a.": city_clean = ""

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(base_dir, 'dms_master.csv')
    
    if not os.path.exists(csv_path):
        csv_path = os.path.join(os.path.dirname(__file__), 'dms_master.csv')
    
    if not os.path.exists(csv_path):
        print(f"[DMS ERROR] File not found at {csv_path}")
        return None

    try:
        with open(csv_path, mode='r', encoding='utf-8-sig', errors='ignore') as f:
            reader = list(csv.DictReader(f)) 
            
            if len(pincode_clean) == 6:
                for row in reader:
                    p_val = (row.get('Pincode') or row.get('Pin') or row.get('pincode') or '').strip()
                    if p_val == pincode_clean:
                        return {
                            "state": (row.get('State') or row.get('state_name') or '').strip(),
                            "city": (row.get('City') or row.get('District') or row.get('city_name') or '').strip(),
                            "pincode": p_val
                        }
            
            if city_clean:
                for row in reader:
                    c_val = (row.get('City') or row.get('District') or row.get('city_name') or '').strip()
                    if city_clean == c_val.lower():
                        return {
                            "state": (row.get('State') or row.get('state_name') or '').strip(),
                            "city": c_val,
                            "pincode": (row.get('Pincode') or row.get('Pin') or row.get('pincode') or '').strip()
                        }
    except Exception as e:
        print(f"[DMS ERROR] Processing failure: {str(e)}")
    
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
        2. LOCALITY: Extract neighborhood. If none, output City name.
        3. PINCODE: Extract 6-digit PIN. If missing, leave empty string.
        4. OWNER NAME: Look for 'Director', 'Founder', or 'Proprietor'.
        5. ALTERNATE PHONE: Use 'N.A.' if none.
        6. JSON FORMAT ONLY. No markdown tags.
        7. LOCATION DETERMINATION: If the extracted city/state is inside Maharashtra, output is_maharashtra: true.
        
        EXPECTED KEYS: { "company_name": "", "owner_name": "", "primary_phone": "", "alternate_phone": "", "email_1": "", "email_2": "", "full_address": "", "locality": "", "state_name": "", "city_name": "", "pincode_value": "", "ai_niche": "", "is_maharashtra": true }
        """

        model = genai.GenerativeModel(model_name="gemini-2.5-flash", generation_config={"response_mime_type": "application/json"})
        ai_response = model.generate_content(system_prompt + "\n\n--- TEXT ---\n" + combined_text[:25000])
        
        try:
            # Check if Gemini blocked the prompt due to safety ratings
            if getattr(ai_response, 'text', None) is None:
                print("[AI ERROR] Gemini blocked the prompt or returned empty.")
                return Response({"error": "Content blocked by AI safety filters"}, status=500)

            res_text = ai_response.text.strip()
            if res_text.startswith("```"):
                res_text = re.sub(r'^```[a-z]*\n|```$', '', res_text, flags=re.MULTILINE)
            extracted_data = json.loads(res_text)
        except Exception as json_err:
            print(f"[JSON ERROR] Failed to parse AI output: {str(json_err)}")
            return Response({"error": f"AI generated invalid JSON: {str(json_err)}"}, status=500)

        # --- BUG FIX 1: Clean Company Name (BULLETPROOFED) ---
        company_name = str(extracted_data.get("company_name") or "N.A.").strip()
        if " by " in company_name.lower():
            company_name = company_name[:company_name.lower().rfind(" by ")].strip()

        # --- BUG FIX 2 & 3: Normalize Location via DMS Master (BULLETPROOFED) ---
        raw_pin = str(extracted_data.get("pincode_value") or "")
        raw_city = str(extracted_data.get("city_name") or "")
        raw_state = str(extracted_data.get("state_name") or "")
        
        verified_loc = normalize_location_from_dms(raw_pin, raw_city)
        
        if verified_loc:
            state_name = str(verified_loc.get("state") or "")
            city_name = str(verified_loc.get("city") or "")
            pincode_value = str(verified_loc.get("pincode") or "")
        else:
            state_name = raw_state
            city_name = raw_city
            pincode_value = re.sub(r'\D', '', raw_pin)[:6]

        is_maharashtra = bool(extracted_data.get("is_maharashtra", True))
        if state_name and "maharashtra" not in state_name.lower():
            is_maharashtra = False
        elif state_name and "maharashtra" in state_name.lower():
            is_maharashtra = True

        # --- BUG FIX 4: Address Formatting (BULLETPROOFED) ---
        full_address = str(extracted_data.get("full_address") or "").strip()
        if pincode_value and pincode_value not in full_address:
            full_address = f"{full_address.strip(', ')}, {city_name} - {pincode_value}".strip(", ")

        # --- PREPARE FINAL PAYLOAD ---
        response_data = {
            "company_name": company_name[:150],
            "owner_name": str(extracted_data.get("owner_name") or "N.A."),
            "primary_phone": str(extracted_data.get("primary_phone") or ""),
            "alternate_phone": str(extracted_data.get("alternate_phone") or "N.A."),
            "email_1": str(extracted_data.get("email_1") or ""),
            "email_2": str(extracted_data.get("email_2") or "N.A."),
            "full_address": full_address,
            "locality": str(extracted_data.get("locality") or city_name),
            "state_name": state_name,
            "city_name": city_name,
            "pincode_value": pincode_value,
            "is_maharashtra": is_maharashtra,
            "ocr_text": ocr_text
        }

        cat_data = match_category_from_csv(combined_text, company_name, str(extracted_data.get("ai_niche") or ""))
        response_data.update(cat_data)

        return Response(response_data)

    except Exception as e:
        print(f"[FATAL SERVER ERROR] {str(e)}")
        return Response({"error": str(e)}, status=500)