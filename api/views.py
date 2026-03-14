import sys
import os
import csv
import json
import requests
import gc
import random
import difflib
import re
import warnings
from io import BytesIO
import pytesseract
from PIL import Image
from rest_framework.decorators import api_view
from rest_framework.response import Response
from dotenv import load_dotenv

# Suppress the deprecation warning so it doesn't clutter your production terminal
warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")
warnings.filterwarnings("ignore", category=UserWarning, module="PIL.Image")
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
# 3. CSV MAPPERS (PENALTY-BASED MATH ENGINE)
# ==========================================
def match_category_from_csv(text, company_name, ai_niche):
    ai_niche_clean = str(ai_niche or '').strip()
    ai_niche_lower = ai_niche_clean.lower()
    
    # Default Result - We pass the exact niche to be typed into txtComment 
    result = {
        "business_category": "Service Provider", 
        "business_sub_category": "", 
        "business_small_category": ai_niche_clean, 
        "category_not_in_list": False,
        "remarks": ai_niche_clean 
    }
    
    if not ai_niche_lower or ai_niche_lower == 'n.a.':
        return result
    
    possible_names = ['category_master.csv', 'category.csv', 'Category.csv', 'Category.xlsx - Final Small Category in DMS wit.csv']
    search_dirs = [os.path.dirname(os.path.abspath(__file__)), os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.getcwd()]
    
    csv_path = None
    for d in search_dirs:
        for name in possible_names:
            p = os.path.join(d, name)
            if os.path.exists(p):
                csv_path = p
                break
        if csv_path: break
    
    if not csv_path:
        return result
        
    best_score = -1
    best_match = None
    
    # Remove generic filler words from AI to find its true core meaning
    generic_words = {'services', 'service', 'company', 'companies', 'agency', 'provider', 'solutions', 'and', '&', 'the', 'in', 'of', 'for', 'business', 'dealers', 'retail', 'design', 'store', 'shop', 'manufacturer', 'manufacturers', 'brand', 'brands', 'online'}
    
    # Map synonyms so "Apparel" natively equals "Clothing"
    norm_map = {'apparel': 'clothing', 'garment': 'clothing', 'garments': 'clothing', 'jewelry': 'jewellery'}
    
    ai_words_raw = set(re.findall(r'\w+', ai_niche_lower))
    ai_words = set(norm_map.get(w, w) for w in ai_words_raw) - generic_words

    # DOMAIN FIREWALL: Words that radically change the context of a business
    restrictives = {
        'car', 'cars', 'auto', 'automobile', 'automobiles', 'vehicle', 'vehicles', 
        'bike', 'bikes', 'motor', 'institute', 'training', 'classes', 'dealer', 
        'repair', 'hospital', 'clinic', 'salon', 'school', 'college', 'medical', 
        'pharma', 'software', 'hardware', 'it', 'restaurant', 'cafe', 'food',
        'real', 'estate', 'property', 'builders', 'export', 'exports', 'exporters',
        'wholesale', 'wholesaler', 'wholesalers', 'industrial', 'machinery',
        'garment', 'garments', 'apparel', 'clothing', 'clothes', 'boutique', 'readymade'
    }
    
    try:
        with open(csv_path, mode='r', encoding='utf-8-sig', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                cat, sub_cat, small_cat = "", "", ""
                synonyms = []
                
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
                
                all_targets = [small_cat.lower()] + synonyms
                
                for target in all_targets:
                    if not target: continue
                    target_clean = target.strip()
                    score = 0
                    
                    t_words_raw = set(re.findall(r'\w+', target_clean))
                    t_words = set(norm_map.get(w, w) for w in t_words_raw) - generic_words
                    
                    # 1. Exact Match = Instant Win
                    if ai_niche_lower == target_clean:
                        score = 1000
                    else:
                        # Prevent high scores from generic single words (like just "Manufacturer")
                        if not ai_words and len(ai_words_raw) > 0:
                            score -= 1000 
                            continue
                            
                        # 2. Spelling/Sequence Similarity Math (Max 100 pts)
                        seq_ratio = difflib.SequenceMatcher(None, ai_niche_lower, target_clean).ratio()
                        score += seq_ratio * 100
                        
                        # 3. Meaningful Word Overlap Math (Max 200 pts)
                        overlap = ai_words.intersection(t_words)
                        if overlap:
                            score += (len(overlap) / max(1, len(ai_words), len(t_words))) * 200
                            
                        # 4. THE FIREWALL PENALTY (-500 pts)
                        if t_words_raw.intersection(restrictives) and not ai_words_raw.intersection(restrictives):
                            score -= 500
                            
                        # Edge Case: The "Accessories" collision trap
                        if 'accessories' in t_words_raw and 'accessories' in ai_words_raw:
                            if 'car' in t_words_raw or 'auto' in t_words_raw or 'automobiles' in t_words_raw:
                                if 'car' not in ai_words_raw and 'auto' not in ai_words_raw:
                                    score -= 500
                    
                    if score > best_score:
                        best_score = score
                        best_match = (cat, sub_cat, small_cat)
                        
        # Raised the threshold to 65 to enforce strict accuracy over loose spelling matches
        if best_match and best_score > 65:
            result["business_category"] = best_match[0] or "Service Provider"
            result["business_sub_category"] = best_match[1]
            result["business_small_category"] = best_match[2]
            
    except Exception as e:
        pass
        
    return result

def normalize_location_from_dms(state_raw, city_raw):
    state_clean = str(state_raw or '').strip().lower()
    city_clean = str(city_raw or '').strip().lower()
    
    if city_clean in ["n.a.", "none", "null"]: city_clean = ""
    if state_clean in ["n.a.", "none", "null"]: state_clean = ""

    if not city_clean and not state_clean:
        return None

    possible_names = ['dms_master.csv', 'dms.csv', 'DMS_master.csv', 'DMS.csv']
    search_dirs = [
        os.path.dirname(os.path.abspath(__file__)),
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        os.getcwd()
    ]
    
    csv_path = None
    for d in search_dirs:
        for name in possible_names:
            p = os.path.join(d, name)
            if os.path.exists(p):
                csv_path = p
                break
        if csv_path: break

    if not csv_path:
        return None

    try:
        with open(csv_path, mode='r', encoding='utf-8-sig', errors='ignore') as f:
            reader = list(csv.DictReader(f)) 
            
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
        8. AI NICHES: Identify the core business products/services. CRITICAL: NEVER output generic single words like "Manufacturer", "Retailer", or "Agency". ALWAYS combine them with the specific product (e.g., "Jewellery Manufacturer"). DO NOT guess or assume based on the brand name. If there is only 1 core service, return a list of 1 string. Max 3.
        
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

        company_name = str(extracted_data.get("company_name") or "N.A.").strip()
        if " by " in company_name.lower():
            company_name = company_name[:company_name.lower().rfind(" by ")].strip()

        raw_pin = extracted_data.get("pincode_value") or ""
        raw_city = extracted_data.get("city_name") or ""
        raw_state = extracted_data.get("state_name") or ""
        
        verified_loc = normalize_location_from_dms(raw_state, raw_city)
        
        if verified_loc:
            state_name = verified_loc["state"]
            city_name = verified_loc["city"]
            pincode_value = verified_loc["pincode"] 
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

        ai_niches = extracted_data.get("ai_niches", [])
        if not isinstance(ai_niches, list):
            ai_niches = [ai_niches] if ai_niches else []
            
        if not ai_niches and extracted_data.get("ai_niche"):
            ai_niches = [extracted_data.get("ai_niche")]
            
        if not ai_niches:
            ai_niches = ["Service Provider"]
            
        # Post-Processing Hallucination Filter: Delete "Garments/Apparel" if those words aren't actually on the website
        filtered_niches = []
        combined_text_lower = combined_text.lower()
        for niche in ai_niches:
            niche_lower = str(niche).lower()
            if 'garment' in niche_lower or 'apparel' in niche_lower or 'clothing' in niche_lower:
                if 'garment' not in combined_text_lower and 'apparel' not in combined_text_lower and 'clothing' not in combined_text_lower:
                    continue # Skip this hallucinated niche
            filtered_niches.append(niche)
            
        # Fallback if the filter deletes everything
        if not filtered_niches:
            filtered_niches = ["Service Provider"]

        categories_list = []
        for niche in filtered_niches:
            niche_clean = str(niche).strip()
            if niche_clean and niche_clean.lower() != "n.a.":
                cat_data = match_category_from_csv(combined_text, company_name, niche_clean)
                categories_list.append(cat_data)
                
        response_data["categories_list"] = categories_list
        
        if categories_list:
            response_data.update(categories_list[0])

        return Response(response_data)

    except Exception as e:
        print(f"[FATAL SERVER ERROR] {str(e)}")
        return Response({"error": str(e)}, status=500)