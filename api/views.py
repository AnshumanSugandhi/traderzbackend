import re
import csv
import os
import requests
from io import BytesIO
import pytesseract
from PIL import Image, ImageEnhance, ImageOps
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import DMSLocation

# CRITICAL WINDOWS FIX: Tell Python exactly where Tesseract is installed
import sys
# Automatically use Windows path locally, but use default system path on the Cloud!
if sys.platform == 'win32':
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

ENTITY_STR = r'Private Limited|Pvt\.?\s*Ltd\.?|Enterprises|Industries|Technologies|Solutions|Group|Corp\.?|LLP|School|College|Academy|Institute|University|Hospital|Clinic|Foundation|Trust|Society|Matrimony|Jewellers|Caterers|Logistics|Motors|Associates|Realtors|Builders|Developers|Agency|Studio|Boutique|Traders|Exports|Imports|Pharma|Diagnostics'

def clean_noise(text):
    if not text: return ""
    text = re.sub(r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s\d{1,2}(?:st|nd|rd|th)?,\s\d{4}\b', '', text)
    text = re.sub(r'^\d+[\.\)]\s+', '', text, flags=re.MULTILINE)
    return text

def sanitize_company_name(name):
    if not name: return "N.A."
    split_patterns = r'(?i)\b(?:Whatsapp Us|WhatsApp|Contact Us|Read More|Call Now|Follow Us|Know More|Click Here|Home|About Us|Buy Now|Shop Now|Chat with us|Toll Free|Log In|Sign Up)\b|\n|---|\|'
    cleaned = re.split(split_patterns, name)[0].strip()
    cleaned = re.sub(r'[\-\|\,\:\.]+$', '', cleaned).strip()
    cleaned = re.sub(r'^(?:the|of|in|at|for|to|and|by|from)\s+', '', cleaned, flags=re.IGNORECASE).strip()
    
    if not re.match(r'(?i)^(University|Institute|Department|College|Faculty)\s+of\b', cleaned):
        end_keywords = r'(?i)(.*?\b(?:' + ENTITY_STR + r'))\b'
        match = re.search(end_keywords, cleaned)
        if match:
            cleaned = match.group(1).strip()
    return cleaned if cleaned else "N.A."

def extract_company_name(raw_title, raw_text, url):
    is_directory = re.search(r'(?i)(search|directory|portal|jobs|career|cutshort|justdial|indiamart)', url)
    if is_directory:
        domain_match = re.search(r'https?://(?:www\.)?([^/\.]+)', url)
        if domain_match: return sanitize_company_name(domain_match.group(1).title())
        return "N.A."

    dev_filter = r'(?:developed|designed|powered|managed|created|website)\s*(?:and|&)?\s*(?:managed)?\s*by\s*.*?(?:pvt\.?\s*ltd\.?|private limited|technologies|solutions|group|media)'
    clean_text = re.sub(dev_filter, '', raw_text, flags=re.IGNORECASE)
    clean_title = re.sub(r'^(welcome to\s+|home(?:page)?\s*[-|:]?\s*|official website(?: of)?\s+)', '', raw_title, flags=re.IGNORECASE).strip()
    
    entity_keywords = r'\b(' + ENTITY_STR + r')\b'

    if clean_title and clean_title.lower() not in ['home', 'homepage']:
        if re.search(entity_keywords, clean_title, flags=re.IGNORECASE):
            clean_part = re.split(r'\||-|:|–|,', clean_title)[0].strip()
            return sanitize_company_name(clean_part.replace('&', 'and'))

    word_pattern = r'(?:[A-Z][a-zA-Z0-9\-\.\']+|(?:and|of|&|in))'
    entity_pattern = r'\b((?:' + word_pattern + r'\s+){1,7}(?:' + ENTITY_STR + r'))\b'
    legal_matches = re.finditer(entity_pattern, clean_text)
    generics = ['secondary school', 'primary school', 'high school', 'public school', 'private limited', 'pvt ltd', 'the school', 'this school']
    seo_buzzwords = ('best ', 'top ', 'leading ', 'affordable ', 'reliable ', 'list of ')
    for match in legal_matches:
        candidate = match.group(1).strip()
        candidate_lower = candidate.lower()
        if candidate_lower not in generics and not candidate_lower.startswith(seo_buzzwords):
            return sanitize_company_name(candidate.replace('&', 'and'))

    clean_part = re.split(r'\||-|:|–', clean_title)[0].strip()
    if clean_part and clean_part.count(',') == 0 and len(clean_part) <= 60:
        return sanitize_company_name(clean_part.replace('&', 'and'))

    copy_match = re.search(r'(?:©|\(c\)|copyright)\s*(?:©|\(c\)|copyright)?\s*(?:[0-9]{4}\s*[-–]\s*)?(?:[0-9]{4})?[\s\.\,]*([A-Za-z][A-Za-z0-9 \t\&\-\.]{3,45})', clean_text, flags=re.IGNORECASE)
    if copy_match and "rights reserved" not in copy_match.group(1).lower():
        extracted = copy_match.group(1).strip().title().replace('&', 'and')
        extracted = re.sub(r'^[0-9\-\.\s]+', '', extracted)
        if len(extracted) > 3:
            return sanitize_company_name(extracted)
    
    domain_match = re.search(r'https?://(?:www\.)?([^/\.]+)', url)
    if domain_match:
        return sanitize_company_name(domain_match.group(1).title())
    return sanitize_company_name(clean_part.split(',')[0].replace('&', 'and'))

def determine_category_and_niche(text, company_name):
    text_lower = text.lower() + " " + company_name.lower()
    scores = {
        "Manufacturer": len(re.findall(r'\b(manufactur|factory|production|fabrication|plant|machinery|industrial)\b', text_lower)),
        "Wholesaler": len(re.findall(r'\b(wholesale|wholesaler|bulk supplier)\b', text_lower)) * 2,
        "Distributor": len(re.findall(r'\b(distributor|distribution|distributing|channel partner)\b', text_lower)) * 2,
        "Supplier": len(re.findall(r'\b(supplier|supplying|supply chain)\b', text_lower)),
        "Trader": len(re.findall(r'\b(trader|trading|export|import|importer|exporter)\b', text_lower)),
        "Service Provider": len(re.findall(r'\b(service|school|college|academy|hospital|clinic|software|consulting|agency|studio|boutique|logistics|matrimony|store)\b', text_lower))
    }
    best_cat = max(scores, key=scores.get)
    if scores[best_cat] == 0: best_cat = "Service Provider"

    niches = {
        "Education": r'\b(school|college|university|academy|institute|education|tuition|coaching)\b',
        "Healthcare": r'\b(hospital|clinic|medical|pharma|diagnostic|healthcare|doctor|physician)\b',
        "IT & Software": r'\b(software|it services|technology|app development|web design|cyber security|tech)\b',
        "Real Estate": r'\b(real estate|builder|developer|property|architect)\b',
        "E-Commerce & Retail": r'\b(e-commerce|retail|store|shop|online store|marketplace)\b',
        "Manufacturing & Engineering": r'\b(manufacturing|factory|industrial|engineering|machinery|chemicals)\b',
        "Logistics & Transport": r'\b(logistics|transport|courier|packers|movers|shipping)\b',
        "Matrimony & Events": r'\b(matrimony|wedding|event planner|caterers|banquet)\b',
        "Food & Beverage": r'\b(restaurant|cafe|bakery|food|beverage|fmcg)\b',
        "Legal & Consulting": r'\b(law firm|lawyer|consultant|ca|chartered accountant|audit|finance)\b'
    }
    niche = "General Business"
    for n, pattern in niches.items():
        if re.search(pattern, text_lower):
            niche = n
            break
    return best_cat, niche

def match_category_from_csv(text, company_name):
    best_cat, niche = determine_category_and_niche(text, company_name)
    
    result = {
        "business_category": best_cat,
        "business_sub_category": "",
        "business_small_category": niche
    }
    
    csv_path = os.path.join(os.path.dirname(__file__), 'category_master.csv')
    if not os.path.exists(csv_path):
        return result
        
    text_lower = (text + " " + company_name).lower()
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
                    
                if small_cat:
                    words = [w for w in re.split(r'\W+', small_cat.lower()) if len(w) > 3 and w not in ['and', 'for', 'the', 'with', 'other']]
                    for word in words:
                        if word in text_lower: score += 2
                            
                if score > max_score and score > 0:
                    max_score = score
                    result["business_category"] = cat if cat else best_cat
                    result["business_sub_category"] = sub_cat
                    result["business_small_category"] = small_cat
    except Exception as e:
        print(f"[CSV ERROR] {str(e)}")
        
    return result

@api_view(['POST'])
def analyze_website(request):
    try:
        data = request.data
        raw_text = data.get('text', '')
        raw_title = data.get('title', 'N.A.')
        target_url = data.get('url', '')
        social_links = data.get('socials', [])
        image_urls = data.get('images', []) 
        
        domain_base = ""
        domain_match = re.search(r'https?://(?:www\.)?([^/\.]+)', target_url)
        if domain_match:
            domain_base = domain_match.group(1).lower()
        
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
        
        combined_text = raw_text + "\n" + ocr_text

        response_data = {
            "company_name": extract_company_name(raw_title, combined_text, target_url)[:150],
            "owner_name": "N.A.", 
            "primary_phone": "",
            "alternate_phone": "N.A.", 
            "email_1": "",
            "email_2": "",  
            "full_address": "",
            "locality": "",
            "state_name": "",
            "city_name": "",
            "pincode_value": "",
            "ocr_text": ocr_text, 
            "business_category": "",
            "business_sub_category": "",
            "business_small_category": ""
        }

        cat_data = match_category_from_csv(combined_text, response_data["company_name"])
        response_data.update(cat_data)

        owner_found = False
        invalid_owner_words = ['updated', 'created', 'designed', 'powered', 'welcome', 'home', 'read', 'click', 'contact', 'about', 'login', 'school', 'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december', 'foundation', 'trust', 'society', 'college', 'university', 'academy', 'institute', 'department', 'journalism', 'media', 'educator', 'reformer', 'tinka', 'limited', 'pvt', 'ltd', 'group', 'company', 'quartered', 'headquartered', 'enterprises', 'contractor', 'solutions', 'lab', 'health', 'hiring', 'takes', 'talent', 'careers', 'jobs', 'recruitment', 'sales', 'marketing', 'team', 'support', 'business', 'office', 'branch', 'customer', 'care', 'info', 'message', 'desk', 'boy', 'girl', 'noco', 'hub', 'center', 'update', 'news', 'blog', 'article', 'portfolio']
        
        titles_1 = r'(?:[Cc][Ee][Oo]|[Ff]ounder|[Cc]o-?[Ff]ounder|[Dd]irector|[Pp]roprietor|[Oo]wner|[Pp]resident|[Pp]rofessor|[Pp]rof|[Hh]ead|[Pp]rincipal)'
        salutation = r'(?:\(\s*[Dd]r\.?\s*\)\s*|[Dd]r\.?\s*)?(?:[Mm]r\.?|[Mm]rs\.?|[Mm]s\.?)?'
        name_capture = r'([A-Z][a-z]{2,15}(?:\s[A-Z][a-z]{2,15}){0,3})'
        
        tier1_pattern = titles_1 + r'[\.\s\:\-\n\,\|]*' + salutation + r'\s*' + name_capture
        for match in re.finditer(tier1_pattern, combined_text):
            candidate = match.group(1).strip()
            candidate_words = candidate.lower().split()
            if not any(word in candidate_words for word in invalid_owner_words):
                response_data["owner_name"] = candidate
                owner_found = True
                break
                
        if not owner_found:
            titles_2 = r'(?:[Mm]anaging\s+[Pp]artner|[Cc]ontact\s+[Pp]erson)'
            tier2_pattern = titles_2 + r'[\.\s\:\-\n\,\|]*' + salutation + r'\s*' + name_capture
            for match in re.finditer(tier2_pattern, combined_text):
                candidate = match.group(1).strip()
                candidate_words = candidate.lower().split()
                if not any(word in candidate_words for word in invalid_owner_words):
                    response_data["owner_name"] = candidate
                    owner_found = True
                    break
            
        if not owner_found and social_links:
            for link in social_links:
                path_only = re.sub(r'^https?://(?:www\.)?[^/]+', '', link)
                url_name_match = re.search(r'/(?:in/)?([A-Za-z]{3,15})[\.\-\_]([A-Za-z]{3,15})/?$', path_only)
                if url_name_match:
                    first = url_name_match.group(1).title()
                    last = url_name_match.group(2).title()
                    invalid_names = ['company', 'official', 'the', 'india', 'www', 'pages', 'groups', 'profile', 'user', 'home', 'org', 'com', 'net', 'edu', 'school', 'academy', 'college', 'university', 'search', 'directory', 'portal']
                    combined_name = (first + last).lower()
                    if first.lower() not in invalid_names and last.lower() not in invalid_names and combined_name != domain_base:
                        response_data["owner_name"] = f"{first} {last}"
                        break

        comp = response_data["company_name"].lower()
        if response_data["company_name"] == "N.A." or len(comp) < 5 or "department" in comp or "school of" in comp:
            if response_data["owner_name"] != "N.A.":
                response_data["company_name"] = response_data["owner_name"]
            else:
                domain_match = re.search(r'https?://(?:www\.)?([^/\.]+)', target_url)
                if domain_match:
                    response_data["company_name"] = sanitize_company_name(domain_match.group(1).title())

        raw_phones = re.findall(r'(?:\+?91[\-\s]?)?(?:0[\-\s]?)?[1-9](?:[\-\s]?\d){9}\b', combined_text)
        unique_phones = []
        for p in raw_phones:
            clean_num = re.sub(r'\D', '', p)
            if len(clean_num) == 12 and clean_num.startswith('91'): clean_num = clean_num[2:]
            if len(clean_num) == 10: clean_num = f"0{clean_num}"
            if len(clean_num) == 11 and clean_num.startswith('0'):
                if clean_num not in unique_phones: unique_phones.append(clean_num)

        if len(unique_phones) >= 1: response_data["primary_phone"] = unique_phones[0]
        if len(unique_phones) >= 2: response_data["alternate_phone"] = unique_phones[1]

        emails = list(dict.fromkeys(re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', combined_text.lower())))
        valid_emails = [e for e in emails if not e.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp'))]
        if len(valid_emails) >= 1: response_data["email_1"] = valid_emails[0]
        if len(valid_emails) >= 2: response_data["email_2"] = valid_emails[1]

        location_found = False

        def filter_junk_address(raw_str):
            if not raw_str: return ""
            s = re.split(r'(?i)(?:©|copyright|all rights reserved|designed by|powered by|developed by|quick links)', raw_str)[0]
            s = re.sub(r'(?i)\b(?:home|about us|contact us|contact|gallery|achievements|admissions|academics|faculty|facilities|alumni|sitemap|vision|mission|curriculum|startup|jobs|entertainment|pubs|latest|news|coaching|institute|academy|upsc|opportunities|days on)\b', '', s)
            s = re.sub(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', '', s)
            s = re.sub(r'(?i)\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\,?\s+\d{4}\b', '', s)
            s = re.sub(r'(?i)\b(?:location|address|registered office|head office|office|ph|phone|email|mob|tel)[\s\:\-]+', '', s)
            s = re.sub(r'\s+', ' ', s)
            s = re.sub(r'^[,\.\-\:\;\|\>\<\s]+|[,\.\-\:\;\|\>\<\s]+$', '', s)
            return s.strip()

        ADDR_KEYWORDS = r'(?:Colony|Nagar|Road|Sector|Plot|Phase|Vihar|Floor|Building|Street|Marg|Bagh|Opposite|Near|P\.?O\.?\b|Dist\b|District|Junction|Jn\b|House|Bhavan|Cross|Main|Apartment|Complex|Tower|Enclave|Avenue|Gali|Lane|Shop\s*No|Room)'

        pin_matches = list(re.finditer(r'\b[1-9][0-9]{2}\s?[0-9]{3}\b', combined_text))
        for match in pin_matches:
            raw_pin = match.group(0)
            clean_pin = re.sub(r'\s+', '', raw_pin) 
            db_loc = DMSLocation.objects.filter(pin_code=clean_pin, is_active=True).first()
            if db_loc:
                response_data["pincode_value"] = db_loc.pin_code
                response_data["city_name"] = db_loc.city_name
                response_data["state_name"] = db_loc.state
                
                addr_candidates = re.findall(r'(.{15,150})\b' + re.escape(raw_pin) + r'\b', combined_text, flags=re.IGNORECASE | re.DOTALL)
                best_raw_chunk = ""
                for chunk in addr_candidates:
                    if re.search(ADDR_KEYWORDS, chunk, re.IGNORECASE) and not re.search(r'\btried to|bought by\b', chunk, re.IGNORECASE):
                        best_raw_chunk = chunk
                
                clean_addr = ""
                extracted_loc = ""
                if best_raw_chunk:
                    clean_addr = filter_junk_address(best_raw_chunk)
                    if len(clean_addr) < 5 or "excellence" in clean_addr.lower() or "nurturing" in clean_addr.lower() or "late rev" in clean_addr.lower():
                        clean_addr = f"{response_data['company_name']}, {db_loc.city_name}"
                    response_data["full_address"] = f"{clean_addr}, {db_loc.state}, India - {db_loc.pin_code}"
                    
                    addr_no_city = re.sub(r'(?i)[\,\-\s\|]*' + re.escape(db_loc.city_name) + r'\b.*$', '', clean_addr).strip()
                    parts = [p.strip() for p in re.split(r'[\,\|]', addr_no_city) if p.strip()]
                    if len(parts) >= 2: extracted_loc = ", ".join(parts[-2:])[:100]
                    elif len(parts) == 1: extracted_loc = " ".join(parts[0].split()[-4:])[:100]
                else:
                    clean_addr = f"{response_data['company_name']}, {db_loc.city_name}"
                    response_data["full_address"] = f"{clean_addr}, {db_loc.state}, India - {db_loc.pin_code}"
                
                comp_name_lower = response_data.get("company_name", "").lower()
                if extracted_loc and (comp_name_lower in extracted_loc.lower() or extracted_loc.lower() in comp_name_lower):
                    extracted_loc = "" 
                if extracted_loc and len(extracted_loc) > 3 and not re.match(r'^\W+$', extracted_loc):
                    response_data["locality"] = extracted_loc
                elif hasattr(db_loc, 'locality_name') and db_loc.locality_name:
                    response_data["locality"] = db_loc.locality_name
                else:
                    response_data["locality"] = db_loc.city_name
                
                location_found = True
                break 

        if not location_found:
            potential_cities = re.findall(r'\b(?:[A-Z][a-zA-Z]{3,19}|[A-Z]{4,19})\b', combined_text)
            places_raw = ["mumbai", "delhi", "bangalore", "bengaluru", "hyderabad", "chennai", "kolkata", "pune", "ahmedabad", "chandigarh", "jaipur", "surat", "lucknow", "kanpur", "nagpur", "indore", "thane", "bhopal", "visakhapatnam", "patna", "vadodara", "ghaziabad", "ludhiana", "agra", "nashik", "noida", "gurugram", "gurgaon", "avadi", "saharanpur", "amritsar", "jalandhar", "mohali", "patiala", "zirkapur"]
            found_places = [p for p in places_raw if p in combined_text.lower()]
            
            ignore_words = {'Contact', 'Address', 'Phone', 'Email', 'School', 'Academy', 'College', 'Home', 'About', 'Links', 'India', 'Website', 'Copyright', 'Reserved', 'Designed', 'Powered', 'Mobile', 'Fax', 'Query', 'Quick', 'Useful', 'Terms', 'Privacy'}
            city_candidates = list(set([c.title() for c in potential_cities if c.title() not in ignore_words] + [p.title() for p in found_places]))
            
            best_city_loc = None
            for cand in city_candidates:
                if cand.lower() == 'delhi': 
                    db_loc = DMSLocation.objects.filter(city_name__iexact="New Delhi", is_active=True).first()
                    if db_loc:
                        best_city_loc = db_loc
                        break

                db_loc = DMSLocation.objects.filter(city_name__iexact=cand, is_active=True).first()
                if db_loc:
                    best_city_loc = db_loc
                    break
                    
            if best_city_loc:
                response_data["city_name"] = best_city_loc.city_name
                response_data["state_name"] = best_city_loc.state
                response_data["pincode_value"] = best_city_loc.pin_code
                
                target_city = best_city_loc.city_name
                addr_candidates = re.findall(r'(.{20,120})\b' + target_city + r'\b', combined_text, flags=re.IGNORECASE | re.DOTALL)
                best_addr = ""
                clean_addr = ""
                extracted_loc = ""
                for cand in addr_candidates:
                    if re.search(ADDR_KEYWORDS, cand, re.IGNORECASE) and not re.search(r'\btried to|bought by\b', cand, re.IGNORECASE):
                        best_addr = cand
                if best_addr:
                    clean_addr = filter_junk_address(best_addr)
                    if len(clean_addr) < 5: clean_addr = f"{response_data['company_name']}, {target_city}"
                    response_data["full_address"] = f"{clean_addr}, {best_city_loc.state}, India - {best_city_loc.pin_code}"
                    addr_no_city = re.sub(r'(?i)[\,\-\s\|]*' + re.escape(best_city_loc.city_name) + r'\b.*$', '', clean_addr).strip()
                    parts = [p.strip() for p in re.split(r'[\,\|]', addr_no_city) if p.strip()]
                    if len(parts) >= 2: extracted_loc = ", ".join(parts[-2:])[:100]
                    elif len(parts) == 1: extracted_loc = " ".join(parts[0].split()[-4:])[:100]
                else:
                    clean_addr = f"{response_data['company_name']}, {target_city}"
                    response_data["full_address"] = f"{clean_addr}, {best_city_loc.state}, India - {best_city_loc.pin_code}"

                comp_name_lower = response_data.get("company_name", "").lower()
                if extracted_loc and (comp_name_lower in extracted_loc.lower() or extracted_loc.lower() in comp_name_lower):
                    extracted_loc = "" 
                if extracted_loc and len(extracted_loc) > 3 and not re.match(r'^\W+$', extracted_loc):
                    response_data["locality"] = extracted_loc
                elif hasattr(best_city_loc, 'locality_name') and best_city_loc.locality_name:
                    response_data["locality"] = best_city_loc.locality_name
                else:
                    response_data["locality"] = best_city_loc.city_name

        return Response(response_data)
        
    except Exception as e:
        return Response({"error": str(e)}, status=500)