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
# ၁။ CONFIGURATION & AUTH
# ==========================================
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
SERVICE_ACCOUNT_ENCODED = os.environ.get('SERVICE_ACCOUNT_JSON')

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-flash-latest')

# ==========================================
# ၂။ GOOGLE SHEETS FUNCTIONS
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
        try:
            cell = sheet.find(str(sender_id), in_column=1)
        except: cell = None
        
        if cell:
            if name != 'N/A': sheet.update_cell(cell.row, 2, name)
            if phone != 'N/A': sheet.update_cell(cell.row, 3, phone)
        else:
            sheet.append_row([str(sender_id), name, phone, "N/A"])
    except: pass

# ==========================================
# ၃။ CORE BOT PROCESS (FULL KNOWLEDGE BASE)
# ==========================================
def handle_bot_process(sid, txt):
    # (က) Data Extraction - JSON syntax fix {{ }}
    extract_prompt = f"Extract Name and Phone from: '{txt}'. Return JSON: {{\"name\": \"...\", \"phone\": \"...\", \"edit\": false}}"
    try:
        ext_res = model.generate_content(extract_prompt).text
        json_match = re.search(r'\{.*\}', ext_res, re.DOTALL)
        if json_match:
            ext_data = json.loads(json_match.group(0))
            if ext_data.get('name') != 'N/A' or ext_data.get('phone') != 'N/A':
                save_data(sid, ext_data.get('name', 'N/A'), ext_data.get('phone', 'N/A'))
    except: pass

    # (ခ) Full Knowledge Base
    current = fetch_data(sid)
    
    kb = """
    သင်ဟာ 'Work Smart with AI' ရဲ့ Professional Sales Admin (ကျွန်တော်) ဖြစ်ပါတယ်။
    
    [ဗဟုသုတဘဏ် - Knowledge Base]
    - AI Sales Content Class: စမည့်ရက် မေလ ၂ ရက် (၂.၅.၂၀၂၆)၊ စနေ၊ တနင်္ဂနွေ ည ၈ နာရီမှ ၉ နာရီခွဲ။
    - သင်တန်းကြေး: ၂၀၀,၀၀၀ ကျပ် (Early Bird Discount: ၁၅၀,၀၀၀ ကျပ်)။
    - ဝန်ဆောင်မှုများ: 
        1. AI Sales Content Creation (150k)
        2. Social Media Design Class (150k)
        3. Chatbot Training (300k)
        4. Auto Bot Service (Custom Price)
    - သင်ကြားမှု: Zoom Live Learning + Telegram Lifetime record access.
    - Certificate: သင်တန်းဆင်းလက်မှတ် (Digital) ပေးအပ်ပါတယ်။
    - နာမ်စား: လူကြီးမင်းကို 'လူကြီးမင်း' ဟုသုံးပြီး မိမိကိုယ်ကို 'ကျွန်တော်' ဟု သုံးပါ။
    """
    
    status_context = "ဒေတာမပြည့်စုံသေးပါ။ နာမည်နှင့် ဖုန်းနံပါတ်ကို ယဉ်ကျေးစွာတောင်းပါ။"
    if "ပြင်" in txt or "wrong" in txt.lower():
        status_context = "User က ဒေတာပြင်ချင်နေတာပါ။ အချက်အလက်အသစ်ကို ယဉ်ကျေးစွာ ပြန်တောင်းပေးပါ။"
    elif current['name'] != 'N/A' and current['phone'] != 'N/A':
        status_context = f"ဒေတာရပြီးသား (နာမည်: {current['name']}, ဖုန်း: {current['phone']}) ဖြစ်သည်။ ဒေတာထပ်မတောင်းပါနှင့်။ မေးခွန်းရှိလျှင် KB ထဲမှ ဖြေကြားပါ။"

    # (ဂ) Response Generation
    final_prompt = f"{kb}\n\nContext: {status_context}\n\nUser Message: {txt}\n\nယဉ်ကျေးစွာ မြန်မာလို ပြန်ဖြေပါ:"
    try:
        reply = model.generate_content(final_prompt).text
        requests.post(f"https://graph.facebook.com/v12.0/me/messages?access_token={PAGE_ACCESS_TOKEN}", 
                      json={"recipient": {"id": sid}, "message": {"text": reply}})
    except: pass

# ==========================================
# ၄။ WEBHOOK (LOOP KILLER)
# ==========================================
@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        if request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge")
        return "Fail", 403
    
    if request.method == 'POST':
        body = request.json
        if body.get("object") == "page":
            for entry in body.get("entry", []):
                for event in entry.get("messaging", []):
                    if "message" in event and "text" in event["message"] and not event["message"].get("is_echo"):
                        sid = event["sender"]["id"]
                        txt = event["message"]["text"]
                        
                        # Thread သုံးပြီး Facebook timeout ကို ကာကွယ်သည်
                        Thread(target=handle_bot_process, args=(sid, txt)).start()
            
            return "EVENT_RECEIVED", 200
    return "Not Found", 404

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
