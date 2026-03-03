import sys
import os
import csv
import json
import requests
from io import BytesIO
import pytesseract
from PIL import Image, ImageOps
from rest_framework.decorators import api_view
from rest_framework.response import Response
from openai import OpenAI
from dotenv import load_dotenv
# 1. CRITICAL OS FIX: Automatically use Windows path locally, but default on Cloud!
if sys.platform == 'win32':
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Load the hidden .env file
load_dotenv()
# 2. DEEPSEEK API SETUP (Secure!)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ai_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

# ==========================================
# STRICT CSV CATEGORY MAPPER
# Ensures the AI's guesses perfectly match your dropdowns
# ==========================================
def match_category_from_csv(text, company_name, ai_niche):
    text_lower = (text + " " + company_name + " " + ai_niche).lower()
    
    result = {
        "business_category": "Service Provider",
        "business_sub_category": "",
        "business_small_category": ai_niche
    }
    
    csv_path = os.path.join(os.path.dirname(__file__), 'category_master.csv')
    if not os.path.exists(csv_path):
        return result
        
    max_score = 0
    try:
        with open(csv_path, mode='r', encoding='utf-8-sig', errors='ignore') as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames
            
            cat_key = next((h for h in headers if 'category' in h.lower() and 'sub' not in h.lower() and 'small' not in h.lower()), 'Category')
            sub_key = next((h for h in headers if 'sub' in h.lower()), 'Sub Category')
            small_key = next((h for h in headers if 'small' in h.lower()), 'Small Category')

            for row in reader:
                cat = row.get(cat_key, '').strip()
                sub_cat = row.get(sub_key, '').strip()
                small_cat = row.get(small_key, '').strip()
                
                if not small_cat and not sub_cat: continue
                
                score = 0
                if small_cat and small_cat.lower() in text_lower: score += 15
                if sub_cat and sub_cat.lower() in text_lower: score += 5
                if ai_niche.lower() in small_cat.lower(): score += 10
                            
                if score > max_score and score > 0:
                    max_score = score
                    result["business_category"] = cat if cat else "Service Provider"
                    result["business_sub_category"] = sub_cat
                    result["business_small_category"] = small_cat
    except Exception as e:
        print(f"[CSV ERROR] {str(e)}")
        
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
        
        # 1. VISION ENGINE (Free Local OCR)
        ocr_text = ""
        for img_url in image_urls:
            try:
                response = requests.get(img_url, timeout=5)
                img = Image.open(BytesIO(response.content))
                
                if img.width > 1200:
                    ratio = 1200 / img.width
                    img = img.resize((1200, int(img.height * ratio)), Image.Resampling.LANCZOS)
                elif img.width < 600:
                    img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
                
                img_gray = img.convert('L') 
                custom_config = r'--oem 3 --psm 11'
                
                text_normal = pytesseract.image_to_string(img_gray, config=custom_config)
                img_inverted = ImageOps.invert(img_gray)
                text_inverted = pytesseract.image_to_string(img_inverted, config=custom_config)
                
                extracted_str = text_normal + "\n" + text_inverted
                if len(extracted_str.strip()) > 5:
                    ocr_text += f"\n {extracted_str} \n"
                    print(f"[VISION] Successfully read text from image: {img_url}")
            except Exception as e:
                print(f"[VISION ERROR] Could not read {img_url}: {str(e)}")
                continue
        
        combined_text = raw_title + "\n" + raw_text + "\n" + ocr_text

        # 2. THE AI BRAIN (DeepSeek JSON Extraction)
        # We tell the AI exactly what JSON shape Chrome needs
        system_prompt = """
        You are an expert data extraction AI. 
        Read the provided website text and extract the business details.
        You MUST output ONLY a valid JSON object with the following exact keys:
        {
            "company_name": "Exact name of the company or school",
            "owner_name": "Name of the founder/director/principal. If none, use 'N.A.'",
            "primary_phone": "10-digit or 11-digit phone number digits only. If none, use ''",
            "alternate_phone": "Secondary phone digits only. If none, use ''",
            "email_1": "Primary email. If none, use ''",
            "email_2": "Secondary email. If none, use ''",
            "full_address": "The physical address of the business",
            "locality": "The local neighborhood, building, or area name",
            "state_name": "Indian State",
            "city_name": "Indian City",
            "pincode_value": "6-digit Indian PIN code",
            "ai_niche": "A 1-3 word description of what the business does (e.g. 'Software', 'High School', 'Plumbing')"
        }
        Do NOT include markdown formatting or extra text. Output JSON only.
        """

        print("[AI] Sending payload to DeepSeek...")
        
        # Call DeepSeek API
        ai_response = ai_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": combined_text[:15000]} # Limit text to save tokens
            ],
            response_format={"type": "json_object"},
            temperature=0.1 # Low temperature for strict factual accuracy
        )
        
        # 3. PARSE AI RESPONSE
        raw_json = ai_response.choices[0].message.content
        extracted_data = json.loads(raw_json)
        
        # Base dictionary to send to Chrome
        response_data = {
            "company_name": extracted_data.get("company_name", "N.A.")[:150],
            "owner_name": extracted_data.get("owner_name", "N.A."), 
            "primary_phone": extracted_data.get("primary_phone", ""),
            "alternate_phone": extracted_data.get("alternate_phone", ""), 
            "email_1": extracted_data.get("email_1", ""),
            "email_2": extracted_data.get("email_2", ""),  
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
        # We pass the AI's "Niche" guess into the strict CSV matcher
        ai_niche_guess = extracted_data.get("ai_niche", "")
        cat_data = match_category_from_csv(combined_text, response_data["company_name"], ai_niche_guess)
        response_data.update(cat_data)

        print(f"[AI SUCCESS] Successfully extracted: {response_data['company_name']}")

        return Response(response_data)
        
    except Exception as e:
        print(f"[CRITICAL BACKEND ERROR] {str(e)}")
        return Response({"error": str(e)}, status=500)
# ==========================================
# EMPLOYEE SECURE LOGIN ENDPOINT
# ==========================================

# Your secure employee database (You can easily change this to a real Database model later!)
EMPLOYEE_CREDENTIALS = {
    "EMP001": "pass123",
    "EMP002": "bot456",
    "admin": "12345"
}

@api_view(['POST'])
def verify_login(request):
    emp_id = request.data.get('emp_id', '').strip()
    emp_pass = request.data.get('emp_pass', '').strip()
    
    # Check if the employee ID exists and password matches
    if emp_id in EMPLOYEE_CREDENTIALS and EMPLOYEE_CREDENTIALS[emp_id] == emp_pass:
        print(f"[AUTH] Access Granted to {emp_id}")
        return Response({
            "status": "success", 
            "token": f"verified_token_{emp_id}"
        })
    else:
        print(f"[AUTH] Failed login attempt for ID: {emp_id}")
        return Response({
            "status": "error", 
            "message": "Invalid ID or Password"
        }, status=401)