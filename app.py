import os
import json
import gspread
import requests
import re
import base64
from threading import Thread
from flask import Flask, request, jsonify
import google.generativeai as genai
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# ==========================================
# ၁။ CONFIGURATION
# ==========================================
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
MANYCHAT_API_KEY = os.environ.get("MANYCHAT_API_KEY") # ManyChat API Key ထည့်ရန်
SERVICE_ACCOUNT_ENCODED = os.environ.get('SERVICE_ACCOUNT_JSON')

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-flash-latest')

# ==========================================
# ၂။ GOOGLE SHEETS HANDLER
# ==========================================
def get_google_creds():
    try:
        if not SERVICE_ACCOUNT_ENCODED: return None
        creds_json = json.loads(base64.b64decode(SERVICE_ACCOUNT_ENCODED).decode("utf-8"))
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        return Credentials.from_service_account_info(creds_json, scopes=scope)
    except: return None

def fetch_data(sender_id):
    try:
        creds = get_google_creds()
        client = gspread.authorize(creds)
        sheet = client.open("WorkSmart_Leads").sheet1
        cell = sheet.find(str(sender_id), in_column=1)
        if cell:
            row = sheet.row_values(cell.row)
            return {"name": row[1] if len(row)>1 else "N/A", "phone": row[2] if len(row)>2 else "N/A"}
    except: pass
    return {"name": "N/A", "phone": "N/A"}

def save_data(sender_id, name, phone):
    try:
        creds = get_google_creds()
        client = gspread.authorize(creds)
        sheet = client.open("WorkSmart_Leads").sheet1
        try: cell = sheet.find(str(sender_id), in_column=1)
        except: cell = None
        if cell:
            if name != 'N/A': sheet.update_cell(cell.row, 2, name)
            if phone != 'N/A': sheet.update_cell(cell.row, 3, phone)
        else:
            sheet.append_row([str(sender_id), name, phone, "N/A"])
    except: pass

# ==========================================
# ၃။ MANYCHAT API SEND FUNCTION
# ==========================================
def send_reply_via_manychat(user_id, text):
    # subscriber_id နေရာမှာ ManyChat ကပေးတဲ့ user_id ကို သုံးပါမယ်
    url = "https://api.manychat.com/fb/sending/sendContent"
    headers = {
        "Authorization": f"Bearer {MANYCHAT_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "subscriber_id": user_id,
        "data": {
            "version": "v2",
            "content": {
                "messages": [{"type": "text", "text": text}]
            }
        }
    }
    try:
        res = requests.post(url, json=payload, headers=headers)
        print(f"ManyChat API Status: {res.status_code}")
    except Exception as e:
        print(f"ManyChat API Error: {e}")

# ==========================================
# ၄။ ASYNC BOT PROCESS (THE LOOP KILLER)
# ==========================================
def handle_bot_process(sid, txt):
    # (က) Data Extraction
    extract_prompt = f"Extract Name and Phone from: '{txt}'. Return JSON: {{\"name\": \"...\", \"phone\": \"...\", \"edit\": false}}"
    try:
        ext_res = model.generate_content(extract_prompt).text
        json_match = re.search(r'\{.*\}', ext_res, re.DOTALL)
        if json_match:
            ext_data = json.loads(json_match.group(0))
            if ext_data.get('name') != 'N/A' or ext_data.get('phone') != 'N/A':
                save_data(sid, ext_data.get('name', 'N/A'), ext_data.get('phone', 'N/A'))
    except: pass

    # (ခ) Knowledge Base
    current = fetch_data(sid)
    kb = """
    သင်ဟာ 'Work Smart with AI' ရဲ့ Professional Sales Admin (ကျွန်တော်) ဖြစ်ပါတယ်။
    [KNOWLEDGE BASE]
    - AI Sales Content Class: မေလ ၂ ရက် (၂.၅.၂၀၂၆) စမည်။ Sat-Sun ည ၈ နာရီ။
    - သင်တန်းကြေး: ၁၅၀,၀၀၀ ကျပ် (Early Bird)။
    - ဝန်ဆောင်မှု: AI Content Creation, Social Media Design, Chatbot Training.
    - နာမ်စား: မိမိကိုယ်ကို 'ကျွန်တော်'၊ လူကြီးမင်းကို 'လူကြီးမင်း' ဟု သုံးပါ။
    """
    
    status_context = "ဒေတာမပြည့်စုံသေးပါ။ နာမည်နှင့် ဖုန်းနံပါတ်ကို ယဉ်ကျေးစွာတောင်းပါ။"
    if current['name'] != 'N/A' and current['phone'] != 'N/A':
        status_context = f"ဒေတာရပြီးသား (နာမည်: {current['name']}, ဖုန်း: {current['phone']}) ဖြစ်သည်။ ဒေတာထပ်မတောင်းပါနှင့်။"

    final_prompt = f"{kb}\n\nContext: {status_context}\n\nUser Message: {txt}\n\nReply in Burmese:"
    try:
        reply = model.generate_content(final_prompt).text
        send_reply_via_manychat(sid, reply)
    except: pass

# ==========================================
# ၅။ ENDPOINT FOR MANYCHAT (FIXED ROUTE)
# ==========================================
@app.route('/manychat', methods=['POST'])
def manychat_webhook():
    try:
        data = request.json
        sid = data.get('user_id')
        txt = data.get('message')
        
        if sid and txt:
            # ManyChat ကို ချက်ချင်း 200 OK ပြန်ပို့ပြီး Loop ကိုသတ်မည်
            Thread(target=handle_bot_process, args=(sid, txt)).start()
            return jsonify({"status": "success", "message": "Task started"}), 200
        else:
            return jsonify({"status": "error", "message": "Missing data"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
