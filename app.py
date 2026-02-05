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
    model = genai.GenerativeModel('gemini-flash-latest')
    user_sessions = {} 
else:
    print("⚠️ GOOGLE_API_KEY missing!")

# ==========================================
# ၂။ KNOWLEDGE BASE (မိတ်ဆွေ ထပ်ဖြည့်ချင်တာတွေကို ဒီမှာ စာသားအတိုင်း ဖြည့်ရုံပါပဲ)
# ==========================================
KNOWLEDGE_BASE = """
[သင်တန်း အချက်အလက်များ]
- AI Sales Content Writing Class: သင်တန်းစမည့်ရက်မှာ May 2nd (2.5.2026) ဖြစ်ပါသည်။
- သင်တန်းအချိန်: အပတ်စဉ် စနေ နှင့် တနင်္ဂနွေ၊ ည ၈:၀၀ မှ ၉:၃၀ အထိ (Zoom Live)။
- သင်တန်းကြေး: ပုံမှန် ၂ သိန်း၊ Early Bird Discount ဖြင့် ၁ သိန်းခွဲ ကျပ်။
- သင်ကြားမည့်ပုံစံ: Zoom ဖြင့် တိုက်ရိုက်သင်ကြားပြီး Telegram တွင် Lifetime Record ပြန်ကြည့်နိုင်ပါသည်။
- သင်တန်းဆင်းလက်မှတ်: Digital Certificate ထုတ်ပေးပါသည်။

[အပ်နှံပြီးသူများအတွက် FAQ]
- သင်တန်းအပ်ပြီးလျှင် Admin မှ ဖုန်းဆက်သွယ်ပြီး Payment အတည်ပြုပါမည်။
- Payment ပြီးလျှင် Telegram Discussion Group သို့ Link ပို့ပေးပါမည်။
- သင်တန်းတက်ရန် Laptop လိုအပ်သော်လည်း ဖုန်းဖြင့်လည်း လေ့လာနိုင်ပါသည်။
- သင်တန်းကြေးကို KPay (သို့) Wave Money ဖြင့် ပေးသွင်းနိုင်ပါသည်။

[ဝန်ဆောင်မှုများ]
- Social Media Design Class: သင်တန်းကြေး ၁ သိန်းခွဲ။
- Chat Bot Training: သင်တန်းကြေး ၃ သိန်း။
- Auto Bot Service: လူကြီးမင်းတို့ လုပ်ငန်းနှင့် အကိုက်ညီဆုံးဖြစ်အောင် Custom ပြုလုပ်ပေးပါသည်။

[စည်းကမ်းချက်များ]
- သင်ဟာ 'Work Smart with AI' ရဲ့ Professional Sales Admin (ယောကျားလေး) ဖြစ်ပါတယ်။
- နာမ်စားကို 'ကျွန်တော်' ဟု သုံးပြီး လူကြီးမင်းတို့အား ယဉ်ကျေးစွာ ဆက်ဆံပါ။
- အချက်အလက်တောင်းလျှင် တစ်ခုချင်းစီ သီးသန့်တောင်းပါ။ ပြောပြီးသား အချက်အလက်ကို ထပ်မတောင်းပါနှင့်။
"""

# ==========================================
# ၃။ GOOGLE SHEETS HANDLER
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
    except: return {"name": "N/A", "phone": "N/A"}
    return {"name": "N/A", "phone": "N/A"}

def save_data(sender_id, data):
    try:
        creds = get_google_creds()
        client = gspread.authorize(creds)
        sheet = client.open("WorkSmart_Leads").sheet1
        try:
            cell = sheet.find(str(sender_id), in_column=1)
        except: cell = None
        
        name, phone = data.get('name', 'N/A'), data.get('phone', 'N/A')
        if cell:
            if name != 'N/A': sheet.update_cell(cell.row, 2, name)
            if phone != 'N/A': sheet.update_cell(cell.row, 3, phone)
        else:
            sheet.append_row([str(sender_id), name, phone, "N/A"])
    except: pass

# ==========================================
# ၄။ SMART CHAT LOGIC (NO LOOP MODE)
# ==========================================
def ask_gemini(sender_id, message):
    # ၁။ Data Extraction (နောက်ကွယ်မှာ တိတ်တဆိတ် လုပ်မယ်)
    ext_prompt = f"Analyze: '{message}'. Extract Name, Phone. If user wants to change info, set 'edit': true. Return JSON ONLY: {{'name': '...', 'phone': '...', 'edit': false}}"
    try:
        res = model.generate_content(ext_prompt)
        ext = json.loads(re.search(r'\{.*\}', res.text, re.DOTALL).group(0))
    except:
        ext = {"name": "N/A", "phone": "N/A", "edit": False}

    # ၂။ ဒေတာ သိမ်းဆည်းခြင်း
    current = fetch_data(sender_id)
    if ext['name'] != 'N/A' or ext['phone'] != 'N/A':
        save_data(sender_id, ext)
        current = fetch_data(sender_id)

    # ၃။ AI အား ပေးမည့် Instruction (Status Check)
    status_instruction = ""
    if ext['edit']:
        status_instruction = "[SYSTEM: User wants to EDIT info. Forget the current status and ask for new details politely.]"
    elif current['name'] != 'N/A' and current['phone'] != 'N/A':
        status_instruction = f"[SYSTEM: DATA COLLECTED. User Name: {current['name']}, Phone: {current['phone']}. Do NOT ask for these again. Focus only on answering questions from KB.]"
    elif current['name'] == 'N/A':
        status_instruction = "[SYSTEM: Name is missing. If user is ready to register, ask for Name politely.]"
    elif current['phone'] == 'N/A':
        status_instruction = f"[SYSTEM: Name is {current['name']}, but Phone is missing. Ask for Phone number.]"

    # ၄။ Final Chatting
    if sender_id not in user_sessions:
        user_sessions[sender_id] = model.start_chat(history=[])

    # KB + Status + User Message ကို ပေါင်းပြီး AI ဆီ ပို့မယ်
    chat_prompt = f"{KNOWLEDGE_BASE}\n\n{status_instruction}\n\nUser Message: {message}"
    
    try:
        return user_sessions[sender_id].send_message(chat_prompt).text
    except:
        return "ခဏနေမှ ပြန်မေးပေးပါခင်ဗျာ။"

# ==========================================
# ၅။ WEBHOOK
# ==========================================
@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        if request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge")
        return "Fail", 403
    
    body = request.json
    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for event in entry.get("messaging", []):
                if "message" in event and "text" in event["message"] and not event["message"].get("is_echo"):
                    sid = event["sender"]["id"]
                    txt = event["message"]["text"]
                    
                    # AI ဆီက အဖြေတောင်းမယ်
                    rep = ask_gemini(sid, txt)
                    
                    # Messenger ဆီ ပြန်ပို့မယ်
                    requests.post(f"https://graph.facebook.com/v12.0/me/messages?access_token={PAGE_ACCESS_TOKEN}", 
                                  json={"recipient": {"id": sid}, "message": {"text": rep}})
    return "OK", 200

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
