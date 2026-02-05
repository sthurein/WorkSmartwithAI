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
    """နောက်ကွယ်ကနေ ဒေတာသိမ်းပေးတဲ့ function (စကားပြောတာ မနှောင့်နှေးအောင်)"""
    try:
        creds = get_google_creds()
        if not creds: return
        client = gspread.authorize(creds)
        sheet = client.open("WorkSmart_Leads").sheet1
        
        try:
            cell = sheet.find(str(sender_id), in_column=1)
        except gspread.exceptions.CellNotFound:
            cell = None

        name, phone, service = lead_data.get('name', 'N/A'), lead_data.get('phone', 'N/A'), lead_data.get('service', 'N/A')
        
        if cell:
            row = cell.row
            if name != 'N/A': sheet.update_cell(row, 2, name)
            if phone != 'N/A': sheet.update_cell(row, 3, phone)
            if service != 'N/A': sheet.update_cell(row, 4, service)
        else:
            sheet.append_row([str(sender_id), name, phone, service])
    except Exception as e:
        print(f"🔴 Sheet Save Error: {e}")

# ==========================================
# ၃။ INTELLIGENT EXTRACTION & RESPONSE
# ==========================================
def ask_gemini(sender_id, user_message):
    # ၁။ လက်ရှိ သိထားပြီးသား data ကို Sheet ထဲက သွားဖတ်မယ့်အစား Session ထဲမှာပဲ ခဏမှတ်ထားမယ်
    # (သို့မဟုတ် AI ကို Context အနေနဲ့ပဲ ပေးလိုက်မယ်)

    knowledge_base = """
    သင်သည် 'Work Smart with AI' ၏ ကျွမ်းကျင်သော Sales Admin (အမျိုးသား) ဖြစ်သည်။ နာမ်စားကို 'ကျွန်တော်' ဟု သုံးပါ။
    လူကြီးမင်းအား အစဉ်အမြဲ ယဉ်ကျေးစွာ ဆက်ဆံပါ။ စက်ရုပ်လို မဟုတ်ဘဲ လူတစ်ယောက်ကဲ့သို့ နွေးထွေးစွာ စကားပြောပါ။

    [သင်ကြားပေးသော ဝန်ဆောင်မှု ၄ ခု]
    1. AI Sales Content Creation: AI ဖြင့် အရောင်း Post ရေးနည်း။ သင်တန်းကြေး ၂၀၀,၀၀၀ ကျပ် (Early Bird: ၁၅၀,၀၀၀ ကျပ်)။ 
    2. Auto Bot Service: Facebook/Telegram အတွက် Auto Bot တည်ဆောက်ပေးခြင်း။
    3. Social Media Design Class: Canva/AI ဖြင့် ပုံထုတ်နည်း။ ၁၅၀,၀၀၀ ကျပ်။
    4. Chat Bot Training: Chatbot တည်ဆောက်နည်း သင်တန်း။ ၃၀၀,၀၀၀ ကျပ်။

    [သင်တန်းနှင့်ဆိုင်သော အချက်အလက်များဩ
    1. Digital Certificate ထုတ်ပေးသည်။
    2. AI Sale Content Creation သင်တန်းစတင်မည့်ရက် ၂.၅.၂၀၂၆, Sat & Sun only, 8:00 PM to 9:30 PM, Duration 1.5 months
    3. ကျန်တဲ့သင်တန်းတွေရဲ့ အချိန်ကို သင်တန်းဖွင့်ဖို့ ရက်သတ်မှတ်ပြီးရင်ပြန်ပြောပါမယ်။
    4. သင်ကြားမည့်ပုံစံ Zoom, Lecturer Slide and recorded video များကို telegram channel တွင်တင်ပေးမယ်။ 
    
    [လုပ်ဆောင်ရမည့် ပန်းတိုင်များ]
    - Customer ၏ မေးခွန်းများကို KB ထဲမှ အခြေခံ၍ သဘာဝကျကျ ဖြေကြားပါ။
    - စိတ်ဝင်စားမှုရှိပါက နာမည် နှင့် ဖုန်းနံပါတ်ကို တောင်းပါ။ (တစ်ပြိုင်တည်း မတောင်းပါနှင့်)
    - Customer က နာမည်/ဖုန်း ပေးပြီးပါက ထပ်မတောင်းပါနှင့်။ "ကျေးဇူးတင်ပါတယ်၊ မှတ်သားထားလိုက်ပါပြီ" ဟု ပြောပြီး ကျန်သည့် မေးခွန်းများကို ဆက်လက်ဆွေးနွေးပါ။
    - ဒေတာ ရပြီးသွားပါက Admin မှ ဖုန်းဖြင့် ဆက်သွယ်မည်ဖြစ်ကြောင်း ပြောပါ။
    - စကားပြောရာတွင် တစ်ခါပြောပြီးသား အချက်အလက်များကို အကြောင်းပြချက်မရှိဘဲ ထပ်ခါတလဲလဲ မပြောပါနှင့်။
    """

    if sender_id not in user_sessions:
        user_sessions[sender_id] = model.start_chat(history=[])
        # ပထမဆုံးအကြိမ်တွင် Admin Personality သွင်းပေးလိုက်ခြင်း
        user_sessions[sender_id].send_message(knowledge_base)

    chat = user_sessions[sender_id]

    # Extraction prompt (နောက်ကွယ်ကနေ ဒေတာထုတ်ဖို့ AI ကို ခိုင်းခြင်း)
    extract_instruct = f"""
    Based on the message: "{user_message}", extract JSON ONLY if you see Name, Phone or Service. 
    Otherwise return {{"status": "no_data"}}. 
    Example: {{"name": "...", "phone": "...", "service": "..."}}
    """
    
    try:
        # ၁။ ဒေတာ ထုတ်ယူခြင်း (Background process အနေနဲ့ သဘောထားပါ)
        extraction_res = model.generate_content(extract_instruct).text
        json_match = re.search(r'\{.*\}', extraction_res, re.DOTALL)
        if json_match:
            lead_data = json.loads(json_match.group(0))
            if lead_data.get("name") or lead_data.get("phone"):
                # Thread သုံးပြီး Sheet ထဲ သိမ်းမယ် (စကားပြောတာ မနှောင့်နှေးစေရန်)
                Thread(target=save_to_sheet_async, args=(sender_id, lead_data)).start()

        # ၂။ စစ်မှန်သော စကားပြောဆိုမှု အပိုင်း
        response = chat.send_message(user_message)
        return response.text
    except Exception as e:
        print(f"🔴 Chat Error: {e}")
        return "ခဏလေးနော်၊ စနစ်ထဲမှာ တစ်ခုခုလွဲနေလို့ပါ။ ခဏနေမှ ပြန်ပြောပေးပါလားခင်ဗျာ။"

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
    requests.post(url, json=payload)

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
