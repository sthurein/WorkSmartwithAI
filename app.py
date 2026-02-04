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
# ၁။ Environment Variables
# ==========================================
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
SERVICE_ACCOUNT_ENCODED = os.environ.get('SERVICE_ACCOUNT_JSON')

# ==========================================
# ၂။ GEMINI SETUP
# ==========================================
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    user_sessions = {} 
else:
    print("⚠️ Error: GOOGLE_API_KEY is missing!")

# ==========================================
# ၃။ GOOGLE SHEETS & EXTRACTION LOGIC
# ==========================================
def get_google_creds():
    try:
        if not SERVICE_ACCOUNT_ENCODED: return None
        try:
            creds_json = json.loads(SERVICE_ACCOUNT_ENCODED)
        except:
            decoded_bytes = base64.b64decode(SERVICE_ACCOUNT_ENCODED)
            decoded_str = decoded_bytes.decode("utf-8")
            creds_json = json.loads(decoded_str)

        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        return Credentials.from_service_account_info(creds_json, scopes=scope)
    except Exception as e:
        print(f"🔴 Credential Error: {e}")
        return None

def save_to_google_sheet(sender_id, extracted_data):
    try:
        creds = get_google_creds()
        if not creds: return

        client = gspread.authorize(creds)
        try:
            sheet = client.open("WorkSmart_Leads").sheet1
        except:
            print("🔴 Error: Sheet not found!")
            return
        
        name = extracted_data.get('name', 'N/A')
        phone = extracted_data.get('phone', 'N/A')
        service = extracted_data.get('service', 'N/A')

        print(f"📝 Saving -> Name: {name}, Phone: {phone}, Service: {service}")

        try:
            cell = sheet.find(str(sender_id), in_column=1)
        except gspread.exceptions.CellNotFound:
            cell = None
        
        if cell:
            row_number = cell.row
            if name not in ['N/A', 'None']: sheet.update_cell(row_number, 2, name)
            if phone not in ['N/A', 'None']: sheet.update_cell(row_number, 3, phone)
            if service not in ['N/A', 'None']: sheet.update_cell(row_number, 4, service)
        else:
            sheet.append_row([str(sender_id), name, phone, service])
            
    except Exception as e:
        print(f"🔴 Google Sheet Error: {e}")

def check_and_extract_lead(sender_id, current_message):
    try:
        history_text = ""
        if sender_id in user_sessions:
            for msg in user_sessions[sender_id].history:
                role = "User" if msg.role == "user" else "Bot"
                history_text += f"{role}: {msg.parts[0].text}\n"
        
        history_text += f"User (Latest): {current_message}\n"

        prompt = f"""
        Analyze conversation. Extract Name, Phone, Service.
        
        [SERVICES]
        1. "AI Content Course"
        2. "Auto Bot Service"
        
        [RULES]
        1. Name: Capture ANY name (Burmese/English).
        2. Phone: Capture ANY phone number.
        3. Service: Match context.
        4. Return JSON ONLY.
        
        History:
        {history_text}
        
        Output: {{"name": "...", "phone": "...", "service": "..."}}
        """
        
        response = model.generate_content(prompt)
        text_response = response.text.strip()
        
        if "```" in text_response:
            text_response = text_response.replace("```json", "").replace("```", "")
            
        json_match = re.search(r'\{.*\}', text_response, re.DOTALL)
        
        if json_match:
            lead_data = json.loads(json_match.group(0))
            save_to_google_sheet(sender_id, lead_data)
            return lead_data 
        else:
            return None
            
    except Exception as e:
        print(f"🔴 Extraction Error: {e}")
        return None

# ==========================================
# ၄။ CHAT LOGIC (NATURAL LANGUAGE)
# ==========================================
def ask_gemini(sender_id, message, extracted_data=None):
    
    # ၁။ Data ရပြီဆိုရင် Bot ကို အမိန့်ပေးမည့် စာ
    system_override = ""
    if extracted_data:
        name = extracted_data.get('name', 'N/A')
        phone = extracted_data.get('phone', 'N/A')
        
        if name != "N/A" and phone != "N/A":
            system_override = f"""
            [SYSTEM COMMAND: DATA RECEIVED]
            User Details: Name={name}, Phone={phone}
            
            ACTION:
            1. STOP ASKING for name/phone.
            2. Confirm receipt naturally in the user's language.
            3. Say something like: "Thanks {name}, we received your phone {phone}."
            """

    # ၂။ Session မရှိသေးရင် အစက ပြန်စမယ်
    if sender_id not in user_sessions:
        system_instruction = [
            {
                "role": "user",
                "parts": """
                You are the Male Admin (ကျွန်တော်) of 'Work Smart with AI'.
                
                [LANGUAGE & TONE]
                - Primary: Natural Burmese (Myanmar).
                - Adaptive: If user speaks English, reply in English.
                - Tech Terms: Use English for terms like "AI, Content, Course, Marketing" (Do not translate these awkwardly).
                - Tone: Professional but friendly (သုံးပါ၊ ခင်ဗျာ).

                [GOAL]
                Answer questions & Collect Name/Phone.
                
                [RULES]
                - Once Name & Phone are collected, STOP ASKING.
                - Keep answers concise.
                """
            },
            { "role": "model", "parts": "Understood. I will speak natural Burmese/English based on context and stop asking once I have data." }
        ]
        user_sessions[sender_id] = model.start_chat(history=system_instruction)

    chat = user_sessions[sender_id]
    
    # ၃။ Message ပို့ခြင်း
    full_message = message
    if system_override:
        full_message = f"{message}\n\n{system_override}"

    for attempt in range(3):
        try:
            response = chat.send_message(full_message)
            return response.text
        except Exception as e:
            time.sleep(1)
            if attempt == 2: return "System Error, please try again later."

# ==========================================
# ၅။ ROUTES
# ==========================================
@app.route('/', methods=['GET'])
def home():
    return "Bot Online (Indentation Fixed)!", 200

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
        print(f"Error: {e}")
        return jsonify({"response": "Error"}), 500

# FACEBOOK HOOK
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
