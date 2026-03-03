import sys
import os
import csv
import json
import requests
import gc
import random
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

# Dynamically load all available Gemini API Keys (up to 5 or more)
AVAILABLE_KEYS = []
for i in range(1, 10):
    key = os.getenv(f"GEMINI_API_KEY_{i}")
    if key:
        AVAILABLE_KEYS.append(key)

# Fallback in case you only defined "GEMINI_API_KEY"
if not AVAILABLE_KEYS and os.getenv("GEMINI_API_KEY"):
    AVAILABLE_KEYS.append(os.getenv("GEMINI_API_KEY"))

# ==========================================
# 2. EMPLOYEE SECURE LOGIN ENDPOINT
# ==========================================
EMPLOYEE_CREDENTIALS = {
    "EMP001": "pass123",
    "EMP002": "bot456",
    "admin": "12345"
}

@api_view(['POST'])
def verify_login(request):
    emp_id = request.data.get('emp_id', '').strip()
    emp_pass = request.data.get('emp_pass', '').strip()
    
    if emp_id in EMPLOYEE_CREDENTIALS and EMPLOYEE_CREDENTIALS[emp_id] == emp_pass:
        print(f"[AUTH] Access Granted to {emp_id}")
        return Response({"status": "success", "token": f"verified_token_{emp_id}"})
    else:
        print(f"[AUTH] Failed login attempt for ID: {emp_id}")
        return Response({"status": "error", "message": "Invalid ID or Password"}, status=401)

# ==========================================
# 3. STRICT CSV CATEGORY MAPPER
# ==========================================
def match_category_from_csv(text, company_name, ai_niche):
    text_lower = (text + " " + company_name + " " + ai_niche).lower()
    
    result = {
        "business_category": "Service Provider",
        "business_sub_category": "",
        "business_small_category": ai_niche,
        "category_not_in_list": False
    }
    
    # Assuming category_master is in the same folder as views.py
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
                if ai_niche.lower() in small_cat.lower(): score += 15
                            
                if score > max_score and score > 0:
                    max_score = score
                    result["business_category"] = cat if cat else "Service Provider"
                    result["business_sub_category"] = sub_cat
                    result["business_small_category"] = small_cat
                    
        if max_score < 15:
            result["category_not_in_list"] = True
            result["business_category"] = "Service Provider"
            
    except Exception as e:
        print(f"[CSV ERROR] {str(e)}")
        result["category_not_in_list"] = True
        
    return result

# ==========================================
# 3.5 STRICT DMS PINCODE MAPPER
# ==========================================
def lookup_pincode_from_dms(city_name):
    if not city_name or city_name == "N.A.": 
        return ""
        
    # Get the parent directory (where manage.py is located)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(base_dir, 'dms_master.csv')
    
    # Fallback to current directory just in case
    if not os.path.exists(csv_path):
        csv_path = os.path.join(os.path.dirname(__file__), 'dms_master.csv')
        
    if not os.path.exists(csv_path): 
        print(f"[DMS] dms_master.csv not found at {csv_path}!")
        return ""
        
    try:
        with open(csv_path, mode='r', encoding='utf-8-sig', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Check possible column names
                city_col = row.get('City', row.get('District', row.get('city_name', ''))).strip()
                pin_col = row.get('Pincode', row.get('Pin', row.get('pincode', ''))).strip()
                
                if city_col and city_name.lower() in city_col.lower() and pin_col:
                    return pin_col[:6] # Return the 6 digit pin
    except Exception as e:
        print(f"[DMS ERROR] {str(e)}")
        
    return ""

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
        
        # --- VISION ENGINE (RAM Optimized for Render) ---
        ocr_text = ""
        for img_url in image_urls[:1]: 
            try:
                response = requests.get(img_url, timeout=5)
                img = Image.open(BytesIO(response.content))
                
                if img.width > 800:
                    ratio = 800 / img.width
                    img = img.resize((800, int(img.height * ratio)), Image.Resampling.LANCZOS)
                elif img.width < 400:
                    img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
                
                img_gray = img.convert('L') 
                custom_config = r'--oem 3 --psm 11'
                text_normal = pytesseract.image_to_string(img_gray, config=custom_config)
                
                if len(text_normal.strip()) > 5:
                    ocr_text += f"\n {text_normal} \n"
                    print(f"[VISION] Successfully read text from image")
                    
                img.close()
                img_gray.close()
                del response, img, img_gray, text_normal
                gc.collect() 
                
            except Exception as e:
                print(f"[VISION ERROR] Could not read image: {str(e)}")
                continue
        
        combined_text = raw_title + "\n" + raw_text + "\n" + str(social_links) + "\n" + ocr_text

        # --- MULTI-KEY AI ROULETTE (Rate Limit Bypass) ---
        if not AVAILABLE_KEYS:
            return Response({"error": "No Gemini API keys configured on server!"}, status=500)
            
        selected_key = random.choice(AVAILABLE_KEYS)
        genai.configure(api_key=selected_key)
        safe_key_name = selected_key[:10] + "..."
        print(f"[AI] Using API Key: {safe_key_name}")

        # --- THE AI BRAIN ---
        system_prompt = """
        You are an expert data extraction AI for lead generation. 
        Read the provided website text and extract the business details into a JSON object.
        
        STRICT RULES:
        1. COMPANY NAME: Extract the core brand name only. YOU MUST STRIP OUT phrases like "by [Name]" or "Powered by". (e.g., "Desi Utpad by Jaya" MUST become "Desi Utpad").
        2. LOCALITY: Extract the neighborhood, street, or local area. If none is found, output the City name as the locality.
        3. PINCODE: Extract the 6-digit PIN. If it is missing from the website, use your world knowledge to provide the correct standard PIN for that city in the 'ai_guessed_pincode' key.
        4. OWNER NAME: Look for 'Director', 'Founder', or 'Proprietor'.
        5. ALTERNATE PHONE: If you cannot find a unique secondary number, YOU MUST enter 'N.A.'.
        6. JSON FORMAT ONLY. No markdown tags.
        7. LOCATION DETERMINATION: Does the address or extracted city belong inside the State of Maharashtra? If Yes, output is_maharashtra: true. If No, output is_maharashtra: false.
        
        EXPECTED KEYS:
        {
            "company_name": "Core brand name",
            "owner_name": "Founder name or N.A.",
            "primary_phone": "10-digit number or empty",
            "alternate_phone": "Secondary number or N.A.",
            "email_1": "Primary email",
            "email_2": "Secondary email or N.A.",
            "full_address": "The physical address",
            "locality": "Neighborhood or City Name",
            "state_name": "Indian State",
            "city_name": "Indian City",
            "pincode_value": "6-digit PIN from text",
            "ai_guessed_pincode": "6-digit PIN from your world knowledge",
            "ai_niche": "A 1-3 word description",
            "is_maharashtra": boolean
        }
        """

        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            generation_config={"response_mime_type": "application/json"}
        )
        
        full_prompt = system_prompt + "\n\n--- WEBSITE TEXT ---\n" + combined_text[:25000]
        ai_response = model.generate_content(full_prompt)
        
        # --- PARSE AI RESPONSE ---
        raw_json = ai_response.text.strip()
        if raw_json.startswith("```json"):
            raw_json = raw_json[7:]
        if raw_json.startswith("```"):
            raw_json = raw_json[3:]
        if raw_json.endswith("```"):
            raw_json = raw_json[:-3]
            
        extracted_data = json.loads(raw_json.strip())
        
        # --- DATA CLEANUP & BUG FIXES ---
        city_name = extracted_data.get("city_name", "")
        locality = extracted_data.get("locality", "")
        pincode_value = extracted_data.get("pincode_value", "")
        full_addr = extracted_data.get("full_address", "")
        company_name = extracted_data.get("company_name", "N.A.")
        
        # FIX 1: Hard-strip "by [Name]" from Company Name
        lower_comp = company_name.lower()
        if " by " in lower_comp:
            idx = lower_comp.rfind(" by ")
            company_name = company_name[:idx].strip()

        # FIX 2: DMS Pincode Lookup Fallback
        if not pincode_value and city_name:
            pincode_value = lookup_pincode_from_dms(city_name)
            if not pincode_value:
                pincode_value = extracted_data.get("ai_guessed_pincode", "")

        # FIX 3: Append Pincode to Address if missing
        if pincode_value and str(pincode_value) not in full_addr:
            if full_addr and full_addr != "N.A.":
                full_addr = f"{full_addr}, {city_name} - {pincode_value}".strip(", ")
            elif city_name:
                full_addr = f"{city_name} - {pincode_value}".strip(", ")

        # FIX 4: Locality Fallback to City
        if not locality or locality == "N.A.":
            locality = city_name
            
        # --- PREPARE FINAL PAYLOAD ---
        response_data = {
            "company_name": company_name[:150],
            "owner_name": extracted_data.get("owner_name", "N.A."), 
            "primary_phone": extracted_data.get("primary_phone", ""),
            "alternate_phone": extracted_data.get("alternate_phone") or "N.A.", 
            "email_1": extracted_data.get("email_1", ""),
            "email_2": extracted_data.get("email_2") or "N.A.",  
            "full_address": full_addr,
            "locality": locality,
            "state_name": extracted_data.get("state_name", ""),
            "city_name": city_name,
            "pincode_value": pincode_value,
            "ocr_text": ocr_text,
            "is_maharashtra": extracted_data.get("is_maharashtra", True)
        }

        # --- ALIGN AI WITH CSV PORTAL RULES ---
        ai_niche_guess = extracted_data.get("ai_niche", "")
        cat_data = match_category_from_csv(combined_text, response_data["company_name"], ai_niche_guess)
        response_data.update(cat_data)

        print(f"[AI SUCCESS] Successfully extracted: {response_data['company_name']}")

        return Response(response_data)
        
    except Exception as e:
        print(f"[CRITICAL BACKEND ERROR] {str(e)}")
        return Response({"error": str(e)}, status=500)