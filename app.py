import os
import json
import time
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
MANYCHAT_API_KEY = os.environ.get("MANYCHAT_API_KEY")

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    # Stability အတွက် gemini-1.5-flash ကို အသုံးပြုထားပါသည်
    model = genai.GenerativeModel('gemini-flash-latest')
    user_sessions = {} 
else:
    print("⚠️ CRITICAL: GOOGLE_API_KEY is missing!")

# ==========================================
# ၂။ GOOGLE SHEETS HANDLER (FORMULA FIX INCLUDED)
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
    except Exception as e:
        print(f"🔴 Credential Error: {e}")
        return None

def save_to_sheet_async(sender_id, lead_data):
    """Excel Formula Error မဖြစ်အောင် ဖုန်းနံပါတ်ရှေ့တွင် ' ခံ၍ သိမ်းပေးသော Logic"""
    try:
        creds = get_google_creds()
        if not creds: return
        client = gspread.authorize(creds)
        sheet = client.open("WorkSmart_Leads").sheet1
        
        try:
            cell = sheet.find(str(sender_id), in_column=1)
        except gspread.exceptions.CellNotFound:
            cell = None

        name = lead_data.get('name', 'N/A')
        phone = lead_data.get('phone', 'N/A')
        service = lead_data.get('service', 'N/A')

        # [PRO FIX] ဖုန်းနံပါတ်ကို Text အဖြစ် သတ်မှတ်ရန် ရှေ့မှ ' ခံခြင်း
        if phone != 'N/A' and phone != '':
            if not str(phone).startswith("'"):
                phone = f"'{phone}"

        if cell:
            row = cell.row
            if name != 'N/A' and name != '': sheet.update_cell(row, 2, name)
            if phone != 'N/A' and phone != '': sheet.update_cell(row, 3, phone)
            if service != 'N/A' and service != '': sheet.update_cell(row, 4, service)
        else:
            sheet.append_row([str(sender_id), name, phone, service])
        print(f"✅ Lead Processed & Fixed: {sender_id}")
    except Exception as e:
        print(f"🔴 Sheet Save Error: {e}")

# ==========================================
# ၃။ CORE BOT LOGIC (HUMAN-LIKE CONVERSATION)
# ==========================================
def ask_gemini(sender_id, user_message):
    
    knowledge_base = """
    သင်သည် 'Work Smart with AI' ၏ ကျွမ်းကျင်သော Sales Admin (အမျိုးသား) ဖြစ်သည်။ နာမ်စားကို 'ကျွန်တော်' ဟု သုံးပါ။
    လူကြီးမင်းအား အမြဲတမ်း ယဉ်ကျေးစွာ ဆက်ဆံပါ။ စက်ရုပ်လို မဟုတ်ဘဲ လူကဲ့သို့ သဘာဝကျကျ စကားပြောပါ။

    [သင်ကြားပေးသော ဝန်ဆောင်မှု ၄ ခု]
    1. AI Sales Content Creation: ၁၅၀,၀၀၀ ကျပ် (Early Bird)။ ၂.၅.၂၀၂၆ စမည်။ Sat & Sun (8:00 PM - 9:30 PM)။
    2. Auto Bot Service: Page/Telegram အတွက် Bot တည်ဆောက်ပေးခြင်း။
    3. Social Media Design Class: Canva/AI ဖြင့် ပုံထုတ်နည်း။ ၁၅၀,၀၀၀ ကျပ်။
    4. Chat Bot Training: Chatbot တည်ဆောက်နည်း သင်တန်း။ ၃၀၀,၀၀၀ ကျပ်။

    [ပန်းတိုင်နှင့် စည်းကမ်း]
    - User ၏ မေးခွန်းကို KB မှ အခြေခံ၍ လူကဲ့သို့ သဘာဝကျကျ အရင်ဖြေပါ။
    - စာပြန်သည့်အခါတိုင်း Message ၏ အဆုံးတွင် User ဆီမှရသော Name, Phone, Service ကို <data>{"name": "...", "phone": "...", "service": "..."}</data> tag တွင် JSON ဖြင့် ထည့်ပါ။
    - နိုင်ငံတကာ ဖုန်းနံပါတ်များကိုလည်း လက်ခံပါ။ (ဥပမာ +65, +66)
    - ဒေတာရပြီးပါက ထပ်မတောင်းပါနှင့်။ Admin မှ ဖုန်းဆက်မည်ဖြစ်ကြောင်း ပြောပါ။
    """

    if sender_id not in user_sessions:
        user_sessions[sender_id] = model.start_chat(history=[])
        user_sessions[sender_id].send_message(knowledge_base)

    chat = user_sessions[sender_id]

    try:
        response_obj = chat.send_message(user_message)
        full_text = response_obj.text

        # <data> tag အတွင်းမှ JSON ကို ထုတ်ယူခြင်း
        data_match = re.search(r'<data>(.*?)</data>', full_text, re.DOTALL)
        clean_reply = re.sub(r'<data>.*?</data>', '', full_text, flags=re.DOTALL).strip()

        if data_match:
            try:
                lead_data = json.loads(data_match.group(1))
                if any(v != 'N/A' for v in lead_data.values()):
                    # ဒေတာသိမ်းရန် Thread ခွဲထုတ်ခြင်း (Response မြန်စေရန်)
                    Thread(target=save_to_sheet_async, args=(sender_id, lead_data)).start()
            except: pass

        return clean_reply
        
    except Exception as e:
        print(f"🔴 Gemini Error: {e}")
        return "ခဏလေးနော်၊ လူကြီးမင်း။ စနစ်က ခဏလေး ကြန့်ကြာနေလို့ပါ။"

# ==========================================
# ၄။ ROUTES
# ==========================================
@app.route('/manychat', methods=['POST'])
def manychat_hook():
    data = request.json
    user_id = str(data.get('user_id'))
    message = data.get('message')
    bot_reply = ask_gemini(user_id, message)
    return jsonify({"response": bot_reply}), 200

@app.route('/webhook', methods=['GET', 'POST'])
def fb_webhook():
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
                        msg = event["message"]["text"]
                        reply = ask_gemini(sid, msg)
                        send_facebook_message(sid, reply)
        return "OK", 200

def send_facebook_message(recipient_id, text):
    url = f"https://graph.facebook.com/v12.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    try: requests.post(url, json=payload)
    except: pass

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
