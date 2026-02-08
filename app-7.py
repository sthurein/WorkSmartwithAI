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
MANYCHAT_API_KEY = os.environ.get("MANYCHAT_API_KEY")

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    # Stability အတွက် Flash model ကို သုံးထားပါသည်
    model = genai.GenerativeModel('gemini-flash-latest')
    user_sessions = {} 
else:
    print("⚠️ CRITICAL: GOOGLE_API_KEY is missing!")

# ==========================================
# ၂။ GOOGLE SHEETS HANDLER (UPDATED LOGIC)
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
    """
    Sales Expert Logic အသစ်:
    1. Status, Last Contacted, Follow-up Count, Stop Follow-up တို့ကို Update လုပ်မည်။
    2. Customer စာပြန်လာပါက Follow-up Count ကို '0' သို့ Reset ချမည်။
    """
    try:
        creds = get_google_creds()
        if not creds: return
        client = gspread.authorize(creds)
        sheet = client.open("WorkSmart_Leads").sheet1
        
        try:
            cell = sheet.find(str(sender_id), in_column=1)
        except gspread.exceptions.CellNotFound:
            cell = None

        # Data Extraction
        name = lead_data.get('name', 'N/A')
        phone = lead_data.get('phone', 'N/A')
        service = lead_data.get('service', 'N/A')
        status = lead_data.get('status', 'N/A')
        stop_followup = lead_data.get('stop_followup', False)

        # Time Stamp (Current Time)
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # [PRO FIX] Phone Formatting (Prevent Excel Error)
        if phone != 'N/A' and phone != '':
            if not str(phone).startswith("'"):
                phone = f"'{phone}"

        if cell:
            # === Existing User (Update) ===
            row = cell.row
            
            # Basic Info Update
            if name != 'N/A' and name != '': sheet.update_cell(row, 2, name)
            if phone != 'N/A' and phone != '': sheet.update_cell(row, 3, phone)
            if service != 'N/A' and service != '': sheet.update_cell(row, 4, service)
            
            # --- New Logic Updates ---
            
            # 1. Lead Status Update
            if status != 'N/A': sheet.update_cell(row, 5, status)
            
            # 2. Last Contacted (Always Update when user replies)
            sheet.update_cell(row, 6, current_time)
            
            # 3. Follow-up Count Reset (Customer Active ဖြစ်သွားလို့ 0 ပြန်ထားမယ်)
            sheet.update_cell(row, 7, 0) 
            
            # 4. Stop Follow-up Checkbox
            if stop_followup:
                sheet.update_cell(row, 8, True) # CheckBox True
                sheet.update_cell(row, 5, "Not Interested") # Force Status

        else:
            # === New User (Insert) ===
            # Order: ID, Name, Phone, Service, Status, LastContact, Count(0), Stop(False)
            sheet.append_row([
                str(sender_id), 
                name, 
                phone, 
                service, 
                status if status != 'N/A' else "New",
                current_time,
                0,     # Initial Follow-up Count is 0
                False  # Stop Follow-up is False
            ])
            
        print(f"✅ Lead Updated: {sender_id} | Count Reset to 0")
    except Exception as e:
        print(f"🔴 Sheet Save Error: {e}")

# ==========================================
# ၃။ CORE BOT LOGIC (SALES EXPERT PERSONALITY)
# ==========================================
def ask_gemini(sender_id, user_message):
    
    knowledge_base = """
    သင်သည် 'Work Smart with AI' ၏ ကျွမ်းကျင်သော Sales Expert (အမျိုးသား) ဖြစ်သည်။ 
    
    [Role & Personality]
    - သင်သည် ရောင်းရန်သီးသန့် ကြိုးစားသူမဟုတ်၊ Customer ၏ အခက်အခဲကို ကူညီဖြေရှင်းပေးသူ (Consultant) ဖြစ်သည်။
    - လေသံကို နွေးထွေးပါ၊ ယုံကြည်မှုရှိပါ၊ သဘာဝကျပါစေ။
    - "ဝယ်ပါ" ဟု တိုက်ရိုက်ပြောမည့်အစား "ဒီနည်းပညာက လူကြီးမင်းလုပ်ငန်းကို ဘယ်လိုကူညီနိုင်လဲ" ဆိုတာကို အသားပေးပြောပါ။

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
    4. 7/24 Auto Sale Chat AI Agent Training: 7/24 ဈေးရောင်းပေးနိုင်သည့်  AI Agent တည်ဆောက်နည်း သင်တန်း။ ၈၀၀,၀၀၀ ကျပ်။ 
    5. Digital Certificate ပေးမည်။
    6. Zoom ဖြင့်သင်ကြားမယ်။ Discussion နဲ့ Video record အတွက် Telegram Chanel ပါဝင်မယ်။ 
    
    [Important]
    - နိုင်ငံတကာ ဖုန်းနံပါတ်များကိုလည်း လက်ခံပါ။ (ဥပမာ +65, +66)
    - User က စာပြန်လာပါက Follow-up လုပ်စရာမလိုတော့ကြောင်း System မှ သိရှိပါမည်။
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
                # Data Save ရန် Thread ခွဲထုတ်ခြင်း (Response မြန်စေရန်)
                Thread(target=save_to_sheet_async, args=(sender_id, lead_data)).start()
            except Exception as e:
                print(f"JSON Parse Error: {e}")

        return clean_reply
        
    except Exception as e:
        print(f"🔴 Gemini Error: {e}")
        return "ခဏလေးနော်၊ လူကြီးမင်း။ စနစ်က ခဏလေး ကြန့်ကြာနေလို့ပါ။"

# ==========================================
# ၄။ ROUTES
# ==========================================
@app.route('/')
def home():
    return "Work Smart AI Bot is Running!", 200

# UptimeRobot အတွက် Ping Route
@app.route('/ping')
def ping():
    return "Pong", 200

@app.route('/manychat', methods=['POST'])
def manychat_hook():
    try:
        data = request.json
        user_id = str(data.get('user_id'))
        message = data.get('message')
        bot_reply = ask_gemini(user_id, message)
        return jsonify({"response": bot_reply}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
                            reply = ask_gemini(sid, msg)
                            send_facebook_message(sid, reply)
            return "OK", 200
        except Exception as e:
            print(f"Webhook Error: {e}")
            return "Error", 500

def send_facebook_message(recipient_id, text):
    url = f"https://graph.facebook.com/v12.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    try: requests.post(url, json=payload)
    except: pass

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
