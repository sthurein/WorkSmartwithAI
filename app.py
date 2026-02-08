import os
import json
import time
import gspread
import requests
import re
import base64
import datetime
from datetime import timedelta
from threading import Thread
from flask import Flask, request, jsonify
from google import genai  # SDK အသစ်
from google.oauth2.service_account import Credentials 

app = Flask(__name__)

# ==========================================
# ၁။ CONFIGURATION & AUTH
# ==========================================
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
SERVICE_ACCOUNT_ENCODED = os.environ.get('SERVICE_ACCOUNT_JSON')

# Admin ဝင်ဖြေရင် Bot ခေတ္တရပ်မည့်ကြာချိန် (စက္ကန့်) - ၃၀၀ စက္ကန့် (၅ မိနစ်)
PAUSE_DURATION = 300 
paused_users = {} 

if GOOGLE_API_KEY:
    # SDK အသစ်၏ Client Setup
    client_ai = genai.Client(api_key=GOOGLE_API_KEY)
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
        except:
            cell = None

        name = lead_data.get('name', 'N/A')
        phone = lead_data.get('phone', 'N/A')
        new_service = lead_data.get('service', 'N/A')
        status = lead_data.get('status', 'N/A')
        stop_followup = lead_data.get('stop_followup', False)
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if phone != 'N/A' and phone != '':
            if not str(phone).startswith("'"):
                phone = f"'{phone}"

        if cell:
            row = cell.row
            if name != 'N/A' and name != '': sheet.update_cell(row, 2, name)
            if phone != 'N/A' and phone != '': sheet.update_cell(row, 3, phone)
            
            if new_service != 'N/A' and new_service != '':
                current_services = sheet.cell(row, 4).value
                if current_services:
                    if new_service not in current_services:
                        updated_service = f"{current_services}, {new_service}"
                        sheet.update_cell(row, 4, updated_service)
                else:
                    sheet.update_cell(row, 4, new_service)
            
            if status != 'N/A': sheet.update_cell(row, 5, status)
            sheet.update_cell(row, 6, current_time)
            sheet.update_cell(row, 7, 0) # Reset Follow-up Count
            
            if stop_followup:
                sheet.update_cell(row, 8, True)
                sheet.update_cell(row, 5, "Not Interested") 
        else:
            sheet.append_row([
                str(sender_id), 
                name, 
                phone, 
                new_service, 
                status if status != 'N/A' else "New",
                current_time,
                0, 
                False
            ])
    except: pass

# ==========================================
# ၃။ CORE BOT LOGIC (With FULL Knowledge Base)
# ==========================================
def ask_gemini(sender_id, user_message):
    
    # Boss ရဲ့ မူရင်း Knowledge Base အပြည့်အစုံ
    knowledge_base = """
    သင်သည် 'Work Smart with AI' ၏ ကျွမ်းကျင်သော Sales Expert (အမျိုးသား) ဖြစ်သည်။ 
    
    [Role & Personality]
    - သင်သည် ရောင်းရန်သီးသန့် ကြိုးစားသူမဟုတ်၊ Customer ၏ အခက်အခဲကို ကူညီဖြေရှင်းပေးသူ (Consultant) ဖြစ်သည်။
    - လေသံကို နွေးထွေးပါ၊ ယုံကြည်မှုရှိပါ၊ သဘာဝကျပါစေ။

    [Sales Logic Constraints]
    1. **Interest Check:** User ၏ စကားကို သုံးသပ်ပါ။ 
       - ဈေးမေးခြင်း၊ အသေးစိတ်မေးခြင်း -> Status: "Interested"
       - ငြင်းဆန်ခြင်း၊ မလိုတော့ဟုပြောခြင်း -> Status: "Not Interested" & Stop: True
    2. **Soft Exit:** အကယ်၍ User က "မလိုတော့ဘူး"၊ "စိတ်မဝင်စားဘူး"၊ "Stop" ဟုပြောလျှင် ယဉ်ကျေးစွာ နှုတ်ဆက်ပြီး စကားဖြတ်ပါ။
    3. **Data Tagging:** - အောက်ပါ JSON format ကို အမြဲတမ်း <data> tag ထဲတွင် ထည့်ပေးပါ။
       - <data>{"name": "...", "phone": "...", "service": "...", "status": "...", "stop_followup": boolean}</data>
       - status values: "New", "Interested", "Not Interested", "Closed"
    
    [Product Info - Knowledge Base]
    1. AI Sales Content Creation: ၁၅၀,၀၀၀ ကျပ် (Early Bird)။ ၂.၅.၂၀၂၆ စမည်။ Sat & Sun (8:00 PM - 9:30 PM)။ သင်တန်းကာလ ၆ ပတ်။ 
    2. Auto Bot Service: Page/Telegram အတွက် Bot တည်ဆောက်ပေးခြင်း။
    3. Social Media Design Class: Canva/AI ဖြင့် ပုံထုတ်နည်း။ ၁၅၀,၀၀၀ ကျပ်။
    4. 7/24 Auto Sale Chat AI Agent Training: 7/24 ဈေးရောင်းပေးနိုင်သည့်  AI Agent တည်ဆောက်နည်း သင်တန်း။ ၈၀၀,၀၀၀ ကျပ်။ 
    5. Digital Certificate ပေးမည်။
    6. Zoom ဖြင့်သင်ကြားမယ်။ Discussion နဲ့ Video record အတွက် Telegram Chanel ပါဝင်မယ်။ 
    
    [Important]
    - နိုင်ငံတကာ ဖုန်းနံပါတ်များကိုလည်း လက်ခံပါ။ (ဥပမာ +65, +66)
    - User ရဲ့ စိတ်ဝင်စားတဲ့ Service တွေကို စာရင်းသွင်းပြီးရင် Google Sheet ထဲမှာ တိုက်စစ်ပြီး User ကို ပြန်ပြပြီး Comfirm ရယူပါ။ 
    """

    try:
        # SDK အသစ် (google-genai) အသုံးပြုပုံ
        response = client_ai.models.generate_content(
            model="gemini-1.5-flash",
            config={'system_instruction': knowledge_base},
            contents=user_message
        )
        full_text = response.text

        data_match = re.search(r'<data>(.*?)</data>', full_text, re.DOTALL)
        clean_reply = re.sub(r'<data>.*?</data>', '', full_text, flags=re.DOTALL).strip()

        if data_match:
            try:
                lead_data = json.loads(data_match.group(1))
                Thread(target=save_to_sheet_async, args=(sender_id, lead_data)).start()
            except: pass

        return clean_reply
        
    except Exception as e:
        print(f"🔴 Gemini Error: {e}")
        return "ခဏလေးစောင့်ပေးပါခင်ဗျာ။ System လေး ပြန်စစ်နေလို့ပါ။"

# ==========================================
# ၄။ ROUTES & WEBHOOK
# ==========================================
@app.route('/')
def home():
    return "Work Smart AI Bot (New SDK) is Running!", 200

@app.route('/ping')
def ping():
    return "Pong", 200

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
                        
                        # Admin Reply Logic
                        if event.get("message", {}).get("is_echo"):
                            recipient_id = event["recipient"]["id"]
                            unpause_time = datetime.datetime.now() + timedelta(seconds=PAUSE_DURATION)
                            paused_users[recipient_id] = unpause_time
                            continue 

                        # User Message Logic
                        if "message" in event and "text" in event["message"]:
                            sid = event["sender"]["id"]
                            msg = event["message"]["text"]

                            if sid in paused_users:
                                if datetime.datetime.now() < paused_users[sid]:
                                    continue 
                                else:
                                    del paused_users[sid]

                            # AI ကို မေးမယ်
                            reply = ask_gemini(sid, msg)
                            send_facebook_message(sid, reply)
            return "OK", 200
        except:
            return "Error", 500

def send_facebook_message(recipient_id, text):
    url = f"https://graph.facebook.com/v12.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    try: requests.post(url, json=payload)
    except: pass

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
