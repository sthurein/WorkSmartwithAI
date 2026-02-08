import os
import json
import time
import gspread
import requests
import re
import base64
import datetime
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
MANYCHAT_API_KEY = os.environ.get("MANYCHAT_API_KEY") # Render Env Var မှာ ထည့်ဖို့မမေ့ပါနဲ့

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
    try:
        creds = get_google_creds()
        if not creds: return
        client = gspread.authorize(creds)
        sheet = client.open("WorkSmart_Leads").sheet1
        
        try:
            cell = sheet.find(str(sender_id), in_column=1)
        except: cell = None

        name = lead_data.get('name', 'N/A')
        phone = lead_data.get('phone', 'N/A')
        service = lead_data.get('service', 'N/A')
        status = lead_data.get('status', 'N/A')
        stop_followup = lead_data.get('stop_followup', False)
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Phone Formatting Fix (Excel Error မတက်အောင်)
        if phone != 'N/A' and phone != '':
            if not str(phone).startswith("'"):
                phone = f"'{phone}"

        if cell:
            row = cell.row
            if name != 'N/A': sheet.update_cell(row, 2, name)
            if phone != 'N/A': sheet.update_cell(row, 3, phone)
            if service != 'N/A': sheet.update_cell(row, 4, service)
            if status != 'N/A': sheet.update_cell(row, 5, status)
            sheet.update_cell(row, 6, current_time)
            sheet.update_cell(row, 7, 0) # Customer ပြန်ဆက်သွယ်ရင် Count Reset
            if stop_followup:
                sheet.update_cell(row, 8, True)
                sheet.update_cell(row, 5, "Not Interested")
        else:
            sheet.append_row([str(sender_id), name, phone, service, status if status != 'N/A' else "New", current_time, 0, False])
            
    except Exception as e:
        print(f"🔴 Sheet Error: {e}")

# ==========================================
# ၃။ SEND TO MANYCHAT (ASYNC REPLY)
# ==========================================
def send_to_manychat(user_id, text):
    # Loop မဖြစ်စေရန် ဒီ Function က အရေးကြီးဆုံးဖြစ်သည်
    if not MANYCHAT_API_KEY: 
        print("🔴 MANYCHAT_API_KEY Missing")
        return
    url = "https://api.manychat.com/fb/sending/sendContent"
    headers = {"Authorization": f"Bearer {MANYCHAT_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "subscriber_id": user_id,
        "data": {"version": "v2", "content": {"messages": [{"type": "text", "text": text}]}}
    }
    try:
        requests.post(url, json=payload, headers=headers)
    except Exception as e:
        print(f"🔴 ManyChat Send Error: {e}")

# ==========================================
# ၄။ CORE PROCESSING (BACKGROUND TASK)
# ==========================================
def process_ai_response(sender_id, user_message):
    # Boss လိုချင်တဲ့ Knowledge Base (စကားရှည်တာ ပျောက်စေမည့် Version)
    knowledge_base = """
    သင်သည် 'Work Smart with AI' ၏ ကျွမ်းကျင်သော Sales Expert (အမျိုးသား) ဖြစ်သည်။ 
    
    [CORE RULES - STRICTLY FOLLOW]
    1. **NO REPETITION:** သင်တန်းအကြောင်း တစ်ခါရှင်းပြပြီးပါက ထပ်မရှင်းပြပါနှင့်။ Customer မေးမှသာ ပြန်ဖြေပါ။
    2. **Keep it Short:** စာပြန်လျှင် စာစီစာကုံးမရေးပါနှင့်။ တိုတိုနှင့် လိုရင်းကိုသာ ဖြေပါ။
    3. **Goal:** Customer က စိတ်ဝင်စားပုံပြလျှင် (သို့မဟုတ်) "ဈေးဘယ်လောက်လဲ" ဟုမေးလျှင် သင်တန်းအပ်ရန် သို့မဟုတ် ဖုန်းနံပါတ်တောင်းရန် (Closing) ကိုသာ ဦးတည်ပါ။ အချက်အလက် ပြန်မရွတ်ပါနှင့်။
    4. Follow up လုပ်ခြင်း ရပ်ပါမည်လို့ Customer ကိုပြန်မပြောရဘူးလေ။​ မင်းက လူတစ်ယောက်လို ပြောဆိုရမှာပါ။ 

    [Role & Personality]
    - သင်သည် ရောင်းရန်သီးသန့် ကြိုးစားသူမဟုတ်၊ Customer ၏ အခက်အခဲကို ကူညီဖြေရှင်းပေးသူ (Consultant) ဖြစ်သည်။
    - လေသံကို နွေးထွေးပါ၊ ယုံကြည်မှုရှိပါ၊ သဘာဝကျပါစေ။

    [Sales Logic Constraints]
    1. **Interest Check:** - ဈေးမေး/အသေးစိတ်မေး -> Status: "Interested"
       - ငြင်းဆန်/မလိုတော့ -> Status: "Not Interested" & Stop: True
    2. **Data Tagging:** - အောက်ပါ JSON format ကို အမြဲတမ်း <data> tag ထဲတွင် ထည့်ပေးပါ။
       - <data>{"name": "...", "phone": "...", "service": "...", "status": "...", "stop_followup": boolean}</data>
       - status values: "New", "Interested", "Not Interested", "Closed"
    3. **International Phone:** နိုင်ငံတကာ ဖုန်းနံပါတ်များကိုလည်း လက်ခံပါ။
    4. **ဖုန်းနံပါတ်, အမည်, Service တွေ ရပြီးရင် Sale close လုပ်ပါ။​

    [Product Info - Knowledge Base]
    1. **AI Sales Content Creation:** 150,000 MMK (Early Bird)၊ 2.5.2026 စမည်၊ Sat & Sun (8:00 PM - 9:30 PM)၊ ၆ ပတ်။ 
    2. **Auto Bot Service:** FB/Telegram Bot တည်ဆောက်ခြင်း။  
    3. **Social Media Design:** 150,000 MMK (Gemini/Canva/Flow/Grok)။
    4. **AI Agent Training:** 800,000 MMK (7/24 Auto Sale)။ Mon,Tue, Wed, 20.4.2016 စမယ်။ 8:00 PM to 9:00 PM, 6 weeks, 900,000 MMK
    
    [Additional Benefits]
    - Digital Certificate ပေးမည်။
    - Zoom သင်ကြားမှု + Telegram Channel (Discussion & Record)။
    """

    if sender_id not in user_sessions:
        user_sessions[sender_id] = model.start_chat(history=[])
        user_sessions[sender_id].send_message(knowledge_base)

    try:
        response = user_sessions[sender_id].send_message(user_message).text
        
        data_match = re.search(r'<data>(.*?)</data>', response, re.DOTALL)
        clean_reply = re.sub(r'<data>.*?</data>', '', response, flags=re.DOTALL).strip()

        if data_match:
            try:
                lead_data = json.loads(data_match.group(1))
                save_to_sheet_async(sender_id, lead_data)
            except: pass
        
        send_to_manychat(sender_id, clean_reply)

    except Exception as e:
        print(f"AI Error: {e}")
        send_to_manychat(sender_id, "စနစ်ပိုင်းဆိုင်ရာ အနည်းငယ် ကြန့်ကြာနေပါသဖြင့် ခဏစောင့်ပေးပါခင်ဗျာ။")
# ==========================================
# ၅။ ROUTES
# ==========================================
@app.route('/')
def home(): return "Work Smart AI Bot is Running!", 200

@app.route('/ping')
def ping(): return "Pong", 200

@app.route('/manychat', methods=['POST'])
def manychat_hook():
    try:
        data = request.json
        user_id = str(data.get('user_id'))
        message = data.get('message')
        
        if user_id and message:
            # 🚨 ချက်ချင်း 200 OK ပြန်ပေးလိုက်မယ် (ဒါက Loop မဖြစ်အောင် ကာကွယ်ပေးတဲ့အပိုင်း)
            # ပြီးမှ Thread နဲ့ AI ကို အလုပ်လုပ်ခိုင်းမယ်
            Thread(target=process_ai_response, args=(user_id, message)).start()
            return jsonify({"status": "processing"}), 200
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"error": "No data"}), 400

@app.route('/webhook', methods=['GET', 'POST'])
def fb_webhook():
    if request.method == 'GET':
        if request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge")
        return "Fail", 403

    if request.method == 'POST':
        try:
            body = request.json
            if body.get("object") == "page":
                for entry in body.get("entry", []):
                    for event in entry.get("messaging", []):
                        if "message" in event and "text" in event["message"] and not event["message"].get("is_echo"):
                            sid = event["sender"]["id"]
                            msg = event["message"]["text"]
                            Thread(target=process_ai_response, args=(sid, msg)).start() 
            return "OK", 200
        except: return "Error", 500

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
