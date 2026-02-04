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
# ၁။ CONFIGURATION
# ==========================================
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
SERVICE_ACCOUNT_ENCODED = os.environ.get('SERVICE_ACCOUNT_JSON')

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    try:
        model = genai.GenerativeModel('gemini-flash-latest')
    except:
        model = genai.GenerativeModel('gemini-1.5-flash')
    user_sessions = {} 
else:
    print("⚠️ CRITICAL: GOOGLE_API_KEY is missing!")

# ==========================================
# ၂။ GOOGLE SHEETS HANDLER (MEMORY READ/WRITE)
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
    except: return None

def fetch_current_lead_data(sender_id):
    try:
        creds = get_google_creds()
        if not creds: return {}
        client = gspread.authorize(creds)
        sheet = client.open("WorkSmart_Leads").sheet1
        try:
            cell = sheet.find(str(sender_id), in_column=1)
            if cell:
                row_values = sheet.row_values(cell.row)
                return {
                    "name": row_values[1] if len(row_values) > 1 else "N/A",
                    "phone": row_values[2] if len(row_values) > 2 else "N/A",
                    "service": row_values[3] if len(row_values) > 3 else "N/A"
                }
        except: return {}
    except: return {}
    return {}

def save_to_google_sheet(sender_id, data):
    try:
        creds = get_google_creds()
        if not creds: return False
        client = gspread.authorize(creds)
        try:
            sheet = client.open("WorkSmart_Leads").sheet1
        except: return False
        
        name = data.get('name', 'N/A')
        phone = data.get('phone', 'N/A')
        service = data.get('service', 'N/A')

        try:
            cell = sheet.find(str(sender_id), in_column=1)
        except gspread.exceptions.CellNotFound:
            cell = None
        
        if cell:
            row = cell.row
            if name != 'N/A': sheet.update_cell(row, 2, name)
            if phone != 'N/A': sheet.update_cell(row, 3, phone)
            if service != 'N/A': sheet.update_cell(row, 4, service)
        else:
            sheet.append_row([str(sender_id), name, phone, service])
        return True
    except: return False

# ==========================================
# ၃။ INTELLIGENT EXTRACTION (MIXED TEXT & EDIT LOGIC)
# ==========================================
def check_and_extract_lead(sender_id, current_message):
    try:
        # Prompt: Extract Entities from Mixed Text
        prompt = f"""
        ACT AS A DATA EXTRACTOR. 
        INPUT: "{current_message}"
        
        TASK: Extract Name, Phone, and Service from the input text.
        
        [RULES]
        1. **NAME:** Look for patterns like "My name is...", "Name:...", or just a name mixed in text. (e.g., "မင်္ဂလာပါ မောင်မောင်ပါ") -> Extract "မောင်မောင်".
        2. **PHONE:** Extract ANY number sequence (e.g., 09.., 09-.., 11111). Ignore spaces/dashes.
        3. **SERVICE:** Match keywords (AI, Bot, Design, Chatbot).
        4. **IGNORE:** Conversational fillers (Hello, Thank you, Khinbyar).
        5. OUTPUT JSON ONLY: {{"name": "...", "phone": "...", "service": "..."}}
        """
        
        response = model.generate_content(prompt)
        text_response = response.text.strip()
        if "```" in text_response:
            text_response = text_response.replace("```json", "").replace("```", "")
        
        json_match = re.search(r'\{.*\}', text_response, re.DOTALL)
        
        extracted_data = {"name": "N/A", "phone": "N/A", "service": "N/A"}
        if json_match:
            extracted_data = json.loads(json_match.group(0))

        # [MEMORY MERGE LOGIC]
        existing_data = fetch_current_lead_data(sender_id)
        
        # New data overwrites old data (This fixes the "Edit" issue automatically)
        final_name = extracted_data.get('name') if extracted_data.get('name') != "N/A" else existing_data.get('name', 'N/A')
        final_phone = extracted_data.get('phone') if extracted_data.get('phone') != "N/A" else existing_data.get('phone', 'N/A')
        final_service = extracted_data.get('service') if extracted_data.get('service') != "N/A" else existing_data.get('service', 'N/A')
        
        merged_data = {
            "name": final_name,
            "phone": final_phone,
            "service": final_service
        }

        # Save ONLY if we found something NEW in this message
        if extracted_data.get('name') != "N/A" or extracted_data.get('phone') != "N/A":
            save_to_google_sheet(sender_id, merged_data)
            
        return merged_data

    except Exception as e:
        print(f"Extraction Error: {e}")
        return None

# ==========================================
# ၄။ CHAT LOGIC (EDIT ALLOWED)
# ==========================================
def ask_gemini(sender_id, message, extracted_data=None):
    
    # [KEY FIX] Check if user wants to EDIT
    # If user says "change", "wrong", "update", "ပြင်", "မှား" -> We ALLOW asking again.
    edit_keywords = ["ပြင်", "change", "မှား", "wrong", "update", "reset", "မဟုတ်"]
    is_editing = any(keyword in message.lower() for keyword in edit_keywords)

    system_override = ""
    
    # Only block if data is full AND user is NOT trying to edit
    if extracted_data and not is_editing:
        name = extracted_data.get('name', 'N/A')
        phone = extracted_data.get('phone', 'N/A')
        
        if name != "N/A" and phone != "N/A":
            system_override = f"""
            [SYSTEM ALERT: DATA COMPLETED]
            User Name: {name}
            User Phone: {phone}
            
            INSTRUCTION:
            1. DO NOT ask for name/phone again unless user wants to change it.
            2. Reply in BURMESE.
            3. Acknowledge receipt: "ကျေးဇူးတင်ပါတယ် {name} ခင်ဗျာ။ ဖုန်းနံပါတ် {phone} ကို လက်ခံရရှိပါတယ်"
            4. Tell them Admin will contact soon.
            """
    
    # If User IS editing, we inject a different override
    if is_editing:
         system_override = f"""
         [SYSTEM ALERT: USER WANTS TO EDIT DATA]
         User wants to change their info.
         INSTRUCTION:
         1. Acknowledge the request.
         2. Ask for the new correct information politely in Burmese.
         """

    if sender_id not in user_sessions:
        system_instruction = [
            {
                "role": "user",
                "parts": """
                You are the Professional Admin of 'Work Smart with AI'. You are male (ကျွန်တော်).
                
                [SERVICES]
                1. "AI Sales Content Creation" (200,000 MMK / Disc: 150,000 MMK).
                2. "Auto Bot Service" (Contact for details).
                3. "Social Media Design Class" (150,000 MMK).
                4. "Chat Bot Training" (300,000 MMK).
                
                [RULES]
                - Speak primarily in **Burmese** (use "လူကြီးမင်း").
                - **Mixed Text:** If user sends "Name is X, Phone is Y", EXTRACT IT immediately.
                - **Editing:** If user says "Wrong phone" or "Change name", ALLOW them to update it.
                - **Stop Loop:** Only stop asking if data is collected AND user is happy.
                - Payment: Admin will contact via phone.
                - Course Platform: Zoom + Telegram.
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
        except:
            time.sleep(1)
            if attempt == 2: return "ခဏနေမှ ပြန်မေးပေးပါခင်ဗျာ။"

# ==========================================
# ၅။ ROUTES
# ==========================================
@app.route('/', methods=['GET'])
def home():
    return "Bot is Live (Mixed Text + Edit Fix)!", 200

@app.route('/manychat', methods=['POST'])
def manychat_hook():
    try:
        data = request.json
        user_id = str(data.get('user_id'))
        message = data.get('message')
        extracted_data = check_and_extract_lead(user_id, message)
        bot_reply = ask_gemini(user_id, message, extracted_data)
        return jsonify({"response": bot_reply}), 200
    except: return jsonify({"response": "Error"}), 500

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
        except: return "ERROR", 500
    return "Not Found", 404

def send_facebook_message(recipient_id, text):
    if not PAGE_ACCESS_TOKEN: return
    url = f"[https://graph.facebook.com/v12.0/me/messages?access_token=](https://graph.facebook.com/v12.0/me/messages?access_token=){PAGE_ACCESS_TOKEN}"
    try: requests.post(url, json={"recipient": {"id": recipient_id}, "message": {"text": text}}, headers={"Content-Type": "application/json"})
    except: pass

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
