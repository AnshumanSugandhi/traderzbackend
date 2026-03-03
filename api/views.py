import sys
import os
import gc
import csv
import json
import requests
from io import BytesIO
import pytesseract
from PIL import Image, ImageOps
from rest_framework.decorators import api_view
from rest_framework.response import Response
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

# 1. OS FIX
if sys.platform == 'win32':
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# 2. GEMINI API SETUP
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ==========================================
# EMPLOYEE SECURE LOGIN ENDPOINT
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
# REFINED CATEGORY MAPPER (School & SOP Focused)
# ==========================================
def match_category_from_csv(text, company_name, ai_niche):
    # Combine text for scoring, prioritizing the School/Education keywords
    text_lower = (text + " " + company_name + " " + ai_niche).lower()
    
    result = {
        "business_category": "Service Provider",
        "business_sub_category": "",
        "business_small_category": ai_niche,
        "category_not_in_list": False  # New flag for the Chrome Extension
    }
    
    csv_path = os.path.join(os.path.dirname(__file__), 'category_master.csv')
    if not os.path.exists(csv_path):
        result["category_not_in_list"] = True
        return result
        
    max_score = 0
    try:
        with open(csv_path, mode='r', encoding='utf-8-sig', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Based on your Category.xlsx: Category, Sub Category, Small Category
                cat = row.get('Category', '').strip()
                sub_cat = row.get('Sub Category', '').strip()
                small_cat = row.get('Small Category', '').strip()
                
                score = 0
                # High priority for Small Category matches (e.g., "High School")
                if small_cat and small_cat.lower() in text_lower: score += 20
                # Medium priority for Sub Category matches
                if sub_cat and sub_cat.lower() in text_lower: score += 10
                # Contextual match from AI niche
                if ai_niche.lower() in small_cat.lower(): score += 15
                            
                if score > max_score:
                    max_score = score
                    result["business_category"] = cat
                    result["business_sub_category"] = sub_cat
                    result["business_small_category"] = small_cat

        # SOP RULE: If the match is weak (score < 15), mark as "Not In List"
        if max_score < 15:
            result["category_not_in_list"] = True
            result["business_category"] = "Service Provider" # Fallback
            
    except Exception as e:
        print(f"[CSV ERROR] {str(e)}")
        result["category_not_in_list"] = True
        
    return result

# ==========================================
# MAIN API ENDPOINT
# ==========================================
@api_view(['POST'])
def analyze_website(request):
    try:
        data = request.data
        raw_text = data.get('text', '')
        raw_title = data.get('title', 'N.A.')
        target_url = data.get('url', '')
        image_urls = data.get('images', []) 
        
        # 1. VISION ENGINE (Memory Optimized for Render Free Tier)
        ocr_text = ""
        
        # STRICT LIMIT: Only process the first image to prevent OOM crashes
        for img_url in image_urls[:1]: 
            try:
                response = requests.get(img_url, timeout=5)
                img = Image.open(BytesIO(response.content))
                
                # RAM SAVER: Resize to max 800px instead of 1200px
                if img.width > 800:
                    ratio = 800 / img.width
                    img = img.resize((800, int(img.height * ratio)), Image.Resampling.LANCZOS)
                elif img.width < 400:
                    img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
                
                img_gray = img.convert('L') 
                custom_config = r'--oem 3 --psm 11'
                
                # RAM SAVER: Only read normal text, drop the inverted read
                text_normal = pytesseract.image_to_string(img_gray, config=custom_config)
                
                if len(text_normal.strip()) > 5:
                    ocr_text += f"\n {text_normal} \n"
                    print(f"[VISION] Successfully read text from image")
                    
                # RAM SAVER: Explicitly delete the heavy image files from memory
                img.close()
                img_gray.close()
                del response, img, img_gray, text_normal
                gc.collect() # Force the server to empty the trash
                
            except Exception as e:
                print(f"[VISION ERROR] Could not read image: {str(e)}")
                continue
        
        combined_text = raw_title + "\n" + raw_text + "\n" + ocr_text

        # 2. THE AI BRAIN (Gemini 1.5 Flash)
        system_prompt = """
        You are an expert data extraction AI. 
        Read the provided website text and extract the business details.
        You MUST output ONLY a valid JSON object with the following exact keys:
        {
            "company_name": "Exact name of the company or school",
            "owner_name": "Name of the founder/director/principal. If none, use 'N.A.'",
            "primary_phone": "10-digit or 11-digit phone number digits only. If none, use ''",
            "alternate_phone": "Secondary phone digits only. If none, use 'N.A.'",
            "email_1": "Primary email. If none, use ''",
            "email_2": "Secondary email. If none, use ''",
            "full_address": "The physical address of the business",
            "locality": "The local neighborhood, building, or area name",
            "state_name": "Indian State",
            "city_name": "Indian City",
            "pincode_value": "6-digit Indian PIN code",
            "ai_niche": "A 1-3 word description of what the business does (e.g. 'Software', 'High School', 'Plumbing')"
        }
        """

        print("[AI] Sending payload to Gemini...")
        
        # Enforce strict JSON output from Gemini
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            generation_config={"response_mime_type": "application/json"}
        )
        full_prompt = system_prompt + "\n\n--- WEBSITE TEXT ---\n" + combined_text[:20000]
        ai_response = model.generate_content(full_prompt)
        
        # 3. PARSE AI RESPONSE
        raw_json = ai_response.text.strip()
        
        # Bulletproof: Strip markdown formatting if Gemini included it
        if raw_json.startswith("```json"):
            raw_json = raw_json[7:]
        if raw_json.startswith("```"):
            raw_json = raw_json[3:]
        if raw_json.endswith("```"):
            raw_json = raw_json[:-3]
            
        extracted_data = json.loads(raw_json.strip())
        
        # Base dictionary to send to Chrome
        response_data = {
            "company_name": extracted_data.get("company_name", "N.A.")[:150],
            "owner_name": extracted_data.get("owner_name", "N.A."), 
            "primary_phone": extracted_data.get("primary_phone", ""),
            "alternate_phone": extracted_data.get("alternate_phone") or "N.A.", 
            "email_1": extracted_data.get("email_1", ""),
            "email_2": extracted_data.get("email_2") or "N.A.",
            "full_address": extracted_data.get("full_address", ""),
            "locality": extracted_data.get("locality", ""),
            "state_name": extracted_data.get("state_name", ""),
            "city_name": extracted_data.get("city_name", ""),
            "pincode_value": extracted_data.get("pincode_value", ""),
            "ocr_text": ocr_text, 
            "business_category": "",
            "business_sub_category": "",
            "business_small_category": ""
        }

        # 4. ALIGN AI WITH CSV PORTAL RULES
        ai_niche_guess = extracted_data.get("ai_niche", "")
        cat_data = match_category_from_csv(combined_text, response_data["company_name"], ai_niche_guess)
        response_data.update(cat_data)

        print(f"[AI SUCCESS] Successfully extracted: {response_data['company_name']}")

        return Response(response_data)
        
    except Exception as e:
        print(f"[CRITICAL BACKEND ERROR] {str(e)}")
        return Response({"error": str(e)}, status=500)