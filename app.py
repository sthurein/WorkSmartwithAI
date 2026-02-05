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

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-flash-latest')
    user_sessions = {} 
else:
    print("⚠️ CRITICAL: GOOGLE_API_KEY is missing!")

# ==========================================
# ၂။ GOOGLE SHEETS HANDLER (PRO PROTECTION)
# ==========================================
def get_google_creds():
    try:
        if not SERVICE_ACCOUNT_ENCODED: return None
        try:
            creds_json = json.loads(SERVICE_ACCOUNT_ENCODED)
        except:
            creds_json = json.loads(base64.b64decode(SERVICE_ACCOUNT_ENCODED).decode("utf-8"))
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
        sheet = client.open("WorkSmart_Leads").sheet1
        
        name = data.get('name', 'N/A')
        phone = data.get('phone', 'N/A')
        service = data.get('service', 'N/A')

        try:
            cell = sheet.find(str(sender_id), in_column=1)
        except gspread.exceptions.CellNotFound:
            cell = None
        
        if cell:
            row = cell.row
            # [PRO LOGIC] Update only valid data
            if name != 'N/A': sheet.update_cell(row, 2, name)
            if phone != 'N/A': sheet.update_cell(row, 3, phone)
            if service != 'N/A': sheet.update_cell(row, 4, service)
        else:
            sheet.append_row([str(sender_id), name, phone, service])
        return True
    except: return False

# ==========================================
# ၃။ INTELLIGENT EXTRACTION (PRO ANALYZER)
# ==========================================
def check_and_extract_lead(sender_id, current_message):
    try:
        prompt = f"""
        ACT AS A DATA ANALYST ENGINE. 
        INPUT: "{current_message}"
        [TASK] Extract Name, Phone, and Service ONLY if explicitly provided.
        [OUTPUT] Return JSON ONLY: {{"name": "...", "phone": "...", "service": "..."}}
        """
        response = model.generate_content(prompt)
        text_response = response.text.strip().replace("```json", "").replace("```", "")
        json_match = re.search(r'\{.*\}', text_response, re.DOTALL)
        
        if json_match:
            extracted_data = json.loads(json_match.group(0))
            existing_data = fetch_current_lead_data(sender_id)
            
            # Smart Merge
            final_data = {
                "name": extracted_data.get('name') if extracted_data.get('name') != "N/A" else existing_data.get('name', 'N/A'),
                "phone": extracted_data.get('phone') if extracted_data.get('phone') != "N/A" else existing_data.get('phone', 'N/A'),
                "service": extracted_data.get('service') if extracted_data.get('service') != "N/A" else existing_data.get('service', 'N/A')
            }

            if extracted_data.get('name') != "N/A" or extracted_data.get('phone') != "N/A":
                save_to_google_sheet(sender_id, final_data)
            return final_data
        return None
    except: return None

# ==========================================
# ၄။ CHAT LOGIC (SMART FLOW CONTROL)
# ==========================================
def ask_gemini(sender_id, message, extracted_data=None):
    edit_keywords = ["ပြင်", "change", "wrong", "မှား", "မဟုတ်", "edit", "reset"]
    is_editing = any(kw in message.lower() for kw in edit_keywords)

    system_override = ""
    if is_editing:
        system_override = "[SYSTEM ALERT: User wants to EDIT. Ask for new info politely in Burmese.]"
    elif extracted_data:
        name, phone = extracted_data.get('name', 'N/A'), extracted_data.get('phone', 'N/A')
        if name != "N/A" and phone != "N/A":
            system_override = f"[SYSTEM ALERT: DATA COMPLETE. Name: {name}, Phone: {phone}. Confirm and answer FAQ questions if any.]"

    if sender_id not in user_sessions:
        sys_instr = """
        You are the Professional Sales Admin of 'Work Smart with AI'. (ကျွန်တော်).
        [KNOWLEDGE BASE]
        - AI Class: Start May 2nd (2.5.2026). Sat-Sun 8PM.
        - Fee: 200,000 MMK (Early Bird: 150,000 MMK).
        - Services: Design Class (150k), Chatbot Training (300k), Auto Bot (Custom).
        [RULES]
        1. Only ask for Name/Phone if missing.
        2. If user wants to edit, acknowledge and ask for the new info.
        3. Answer FAQ from Knowledge Base directly.
        """
        user_sessions[sender_id] = model.start_chat(history=[{"role": "user", "parts": sys_instr}, {"role": "model", "parts": "Understood."}])

    full_message = f"{message}\n\n{system_override}" if system_override else message
    try:
        response = user_sessions[sender_id].send_message(full_message)
        return response.text
    except:
        return "ခဏနေမှ ပြန်မေးပေးပါခင်ဗျာ။"

# ==========================================
# ၅။ WEBHOOK & ROUTES
# ==========================================
@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        if request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge")
        return "Failed", 403
    
    body = request.json
    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for event in entry.get("messaging", []):
                if "message" in event and "text" in event["message"] and not event["message"].get("is_echo"):
                    sid, txt = event["sender"]["id"], event["message"]["text"]
                    ext = check_and_extract_lead(sid, txt)
                    rep = ask_gemini(sid, txt, ext)
                    requests.post(f"https://graph.facebook.com/v12.0/me/messages?access_token={PAGE_ACCESS_TOKEN}", 
                                  json={"recipient": {"id": sid}, "message": {"text": rep}})
    return "OK", 200

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
