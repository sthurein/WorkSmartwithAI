import os
import json
import time
import gspread
import requests
import re
import base64
from flask import Flask, request, jsonify
import google.generativeai as genai
from google.oauth2.service_account import Credentials 

app = Flask(__name__)

# ==========================================
# ၁။ CONFIGURATION & AUTH
# ==========================================
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
SERVICE_ACCOUNT_ENCODED = os.environ.get('SERVICE_ACCOUNT_JSON')

# [MODEL SETTING] Using 'gemini-flash-latest' as requested
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-flash-latest')
    user_sessions = {} 
else:
    print("⚠️ CRITICAL: GOOGLE_API_KEY is missing!")

# ==========================================
# ၂။ GOOGLE SHEETS HANDLER
# ==========================================
def get_google_creds():
    try:
        if not SERVICE_ACCOUNT_ENCODED:
            print("🔴 Error: Service Account Key not found.")
            return None
        
        try:
            creds_json = json.loads(SERVICE_ACCOUNT_ENCODED)
        except:
            try:
                decoded_bytes = base64.b64decode(SERVICE_ACCOUNT_ENCODED)
                decoded_str = decoded_bytes.decode("utf-8")
                creds_json = json.loads(decoded_str)
            except Exception as e:
                print(f"🔴 Base64 Decode Failed: {e}")
                return None

        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        return Credentials.from_service_account_info(creds_json, scopes=scope)
    except Exception as e:
        print(f"🔴 Credential Error: {e}")
        return None

def save_to_google_sheet(sender_id, extracted_data):
    try:
        creds = get_google_creds()
        if not creds: return False

        client = gspread.authorize(creds)
        
        try:
            sheet = client.open("WorkSmart_Leads").sheet1
        except Exception as e:
            print(f"🔴 Sheet Access Error: {e}")
            return False
        
        name = extracted_data.get('name', 'N/A')
        phone = extracted_data.get('phone', 'N/A')
        service = extracted_data.get('service', 'N/A')

        print(f"📝 Processing Lead -> Name: {name} | Phone: {phone} | Service: {service}")

        try:
            cell = sheet.find(str(sender_id), in_column=1)
        except gspread.exceptions.CellNotFound:
            cell = None
        
        if cell:
            row = cell.row
            if name != 'N/A': sheet.update_cell(row, 2, name)
            if phone != 'N/A': sheet.update_cell(row, 3, phone)
            if service != 'N/A': sheet.update_cell(row, 4, service)
            print(f"✅ Updated Row {row}")
        else:
            sheet.append_row([str(sender_id), name, phone, service])
            print(f"✅ Created New Row")
            
        return True
            
    except Exception as e:
        print(f"🔴 Google Sheet Write Error: {e}")
        return False

# ==========================================
# ၃။ INTELLIGENT EXTRACTION (SPECIFIC SERVICES)
# ==========================================
def check_and_extract_lead(sender_id, current_message):
    try:
        history_text = ""
        if sender_id in user_sessions:
            for msg in user_sessions[sender_id].history:
                role = "User" if msg.role == "user" else "Bot"
                history_text += f"{role}: {msg.parts[0].text}\n"
        
        history_text += f"User (Latest): {current_message}\n"

        # [SERVICE NAMES IN BURMESE]
        prompt = f"""
        Analyze the conversation. Extract Name, Phone, and Interested Service.
        
        [YOUR SPECIFIC SERVICES]
        1. "AI Sales Content Creation" (AI နဲ့ အရောင်း Post တင်ဖို့ Content ဖန်တီးနည်း). သင်တန်းကြေး ၂၀၀,၀၀၀ ကျပ်, Early Bird Discount ၁၅၀,၀၀၀ ကျပ်
        2. "Auto Bot Service" (Auto Bot ဝန်ဆောင်မှု). ဒီအတွက် ဝန်ဆောင်မှုက အသေးစိတ်ဆက်သွယ်ပေးပါမယ်။ 
        3. Social Media Content တွေအတွက် ပုံတွေ, Logo တွေထုတ်နည်း။ သင်တန်းကြေး ၁၅၀,၀၀၀ ကျပ်။ 
        4. Facebook Page, Telegram တွေကို အရောင်းတက်စေဖို့ Chat Bot ထောင်နည်းသင်တန်း၊ ၃၀၀,၀၀၀ ကျပ်။ 
        
        [EXTRACTION RULES]
        1. **NAME:** Capture ANY name (Burmese or English).
        2. **PHONE:** Capture 09... or +959... numbers.
        3. **SERVICE:** Map user interest to one of the 2 services above.
        4. IF MISSING: Use "N/A".
        5. OUTPUT FORMAT: STRICT JSON only.

        [Another Rules]
        1. သင်ဟာ ယောကျားလေးတစ်ယောက်ပါ။ 
        2. တစ်ခြားသင်တန်းအကြောင်းတွေမပြောနဲ့။ ငါတို့ရဲ့ Service အကြောင်းပဲပြောပါ။ 
        3. သင်တန်းကြေးပေးသွင်းဖို့အတွက် Page Admin က ဖုန်းဆက်ပြီး ဆောင်ရွက်ပေးပါမယ်။ ခနစောင့်ပေးပါနော်။ 
        
        
        Conversation:
        {history_text}
        
        JSON Output: {{"name": "...", "phone": "...", "service": "..."}}
        """
        
        response = model.generate_content(prompt)
        text_response = response.text.strip()
        
        if "```" in text_response:
            text_response = text_response.replace("```json", "").replace("```", "")
        
        json_match = re.search(r'\{.*\}', text_response, re.DOTALL)
        
        if json_match:
            lead_data = json.loads(json_match.group(0))
            if lead_data.get('name') != "N/A" or lead_data.get('phone') != "N/A":
                save_to_google_sheet(sender_id, lead_data)
                return lead_data
        return None
            
    except Exception as e:
        print(f"🔴 Extraction Error: {e}")
        return None

# ==========================================
# ၄။ CHAT LOGIC (LOOP KILLER)
# ==========================================
def ask_gemini(sender_id, message, extracted_data=None):
    
    # 1. System Injection
    system_override = ""
    if extracted_data:
        name = extracted_data.get('name', 'N/A')
        phone = extracted_data.get('phone', 'N/A')
        
        if name != "N/A" and phone != "N/A":
            system_override = f"""
            [SYSTEM ALERT: DATA SAVED]
            User Name: {name}
            User Phone: {phone}
            INSTRUCTION: DO NOT ask for name/phone again. Reply in BURMESE. 
            Say: "ကျေးဇူးတင်ပါတယ် {name} ခင်ဗျာ။ ဖုန်းနံပါတ် {phone} ကို လက်ခံရရှိပါတယ်"
            """

    # 2. Init Chat
    if sender_id not in user_sessions:
        system_instruction = [
            {
                "role": "user",
                "parts": """
                You are the Professional Admin of 'Work Smart with AI'.
                [SERVICES]
                1. **AI Sales Content Creation** (AI နဲ့ အရောင်း Post တင်ဖို့ Content ဖန်တီးနည်း).
                2. **Auto Bot Service** (Auto Bot ဝန်ဆောင်မှု).
                [PROTOCOL]
                - Speak primarily in **Burmese**.
                - If user speaks English, adapt to English.
                - STOP asking for Name/Phone once collected.
                """
            },
            { "role": "model", "parts": "Understood." }
        ]
        user_sessions[sender_id] = model.start_chat(history=system_instruction)

    chat = user_sessions[sender_id]
    
    full_message = message
    if system_override:
        full_message = f"{message}\n\n{system_override}"

    for attempt in range(3):
        try:
            response = chat.send_message(full_message)
            return response.text
        except Exception as e:
            time.sleep(1)
            if attempt == 2: return "စနစ်ပိုင်းဆိုင်ရာ Error ဖြစ်နေပါသဖြင့် ခဏနေမှ ပြန်မေးပေးပါခင်ဗျာ။"

# ==========================================
# ၅။ ROUTES
# ==========================================
@app.route('/', methods=['GET'])
def home():
    return "Bot is Live (Fixed Syntax)!", 200

# MANYCHAT HOOK
@app.route('/manychat', methods=['POST'])
def manychat_hook():
    try:
        data = request.json
        user_id = str(data.get('user_id'))
        message = data.get('message')
        
        extracted_data = check_and_extract_lead(user_id, message)
        bot_reply = ask_gemini(user_id, message, extracted_data)
        
        return jsonify({"response": bot_reply}), 200
    except Exception as e:
        print(f"ManyChat Error: {e}")
        return jsonify({"response": "Error"}), 500

# FACEBOOK HOOK (SYNTAX ERROR FIX IS HERE)
@app.route('/webhook', methods=['GET', 'POST'])
def fb_webhook_main():
    if request.method == 'GET':
        if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge")
        return "Verification Failed", 403

    if request.method == 'POST':
        try:
            body = request.json
            if body.get("object") == "page":
                for entry in body.get("entry", []):
                    for event in entry.get("messaging", []):
                        if "message" in event and "text" in event["message"] and not event["message"].get("is_echo"):
                            sender_id = event["sender"]["id"]
                            user_text = event["message"]["text"]
                            
                            extracted = check_and_extract_lead(sender_id, user_text)
                            reply = ask_gemini(sender_id, user_text, extracted)
                            
                            send_facebook_message(sender_id, reply) 
                        
            return "EVENT_RECEIVED", 200
        except Exception as e:
            print(f"Webhook Error: {e}")
            return "ERROR", 500
            
    return "Not Found", 404

def send_facebook_message(recipient_id, text):
    if not PAGE_ACCESS_TOKEN: return
    url = f"[https://graph.facebook.com/v12.0/me/messages?access_token=](https://graph.facebook.com/v12.0/me/messages?access_token=){PAGE_ACCESS_TOKEN}"
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    headers = {"Content-Type": "application/json"}
    try:
        requests.post(url, json=payload, headers=headers)
    except Exception as e:
        print(f"FB Send Error: {e}")

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
