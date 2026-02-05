import os
import json
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
else:
    print("⚠️ GOOGLE_API_KEY missing!")

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
# ၃။ CHAT LOGIC (ANTI-LOOP & HIGH STABILITY)
# ==========================================
def ask_gemini(sender_id, user_message):
    # ၁။ လက်ရှိ Sheet ထဲက status ကို အရင်ကြည့်မယ်
    current = fetch_data(sender_id)
    
    # ၂။ AI ကို လက်ရှိစာထဲက ဒေတာထုတ်ခိုင်းမယ် (Extraction)
    extract_prompt = f"""
    User Message: "{user_message}"
    Task: Extract Name and Phone if present. 
    If user wants to "change/edit/wrong/ပြင်/မှား/မဟုတ်ဘူး", set "edit": true.
    Return JSON ONLY: {{"name": "...", "phone": "...", "edit": false}}
    """
    try:
        ext_res = model.generate_content(extract_prompt).text
        ext_data = json.loads(re.search(r'\{.*\}', ext_res, re.DOTALL).group(0))
        
        # ၃။ ဒေတာအသစ်ပါရင် သိမ်းမယ်
        if ext_data['name'] != 'N/A' or ext_data['phone'] != 'N/A':
            save_data(sender_id, ext_data['name'], ext_data['phone'])
            current = fetch_data(sender_id) # status refresh
    except:
        ext_data = {"edit": False}

    # ၄။ စကားပြန်ပြောမည့်အပိုင်း (No History Mode)
    knowledge_base = """
    သင်ဟာ 'Work Smart with AI' ရဲ့ Professional Sales Admin တစ်ယောက်ပါ။
    - သင်တန်းစမည့်ရက်: မေလ ၂ ရက် (၂.၅.၂၀၂၆)၊ စနေ၊ တနင်္ဂနွေ ည ၈ နာရီမှ ၉ နာရီခွဲ။
    - သင်တန်းကြေး: ၂၀၀,၀၀၀ ကျပ် (Early Bird: ၁၅၀,၀၀၀ ကျပ်)။
    - ဝန်ဆောင်မှုများ: AI Content Class, Social Media Design (150k), Chatbot Training (300k)။
    - သင်ကြားမှု: Zoom Live + Telegram Lifetime Records။
    - နာမ်စား: 'ကျွန်တော်' ကိုသုံးပါ။ လူကြီးမင်းကို 'လူကြီးမင်း' ဟု သုံးပါ။
    - Payment: Admin မှ ဖုန်းဆက်သွယ်ပြီးမှ ပေးသွင်းရပါမည်။
    """

    status_context = ""
    if ext_data.get('edit'):
        status_context = "User က အချက်အလက်မှားလို့ ပြင်ချင်တာပါ။ နာမည် သို့မဟုတ် ဖုန်းနံပါတ်အသစ်ကို ယဉ်ကျေးစွာ ထပ်တောင်းပါ။"
    elif current['name'] != 'N/A' and current['phone'] != 'N/A':
        status_context = f"ဒေတာရပြီးသားဖြစ်သည် (နာမည်: {current['name']}, ဖုန်း: {current['phone']})။ ထပ်မတောင်းပါနဲ့။ လူကြီးမင်းအတွက် ဘာများကူညီပေးရမလဲဟုသာ မေးပါ။"
    else:
        status_context = "နာမည် သို့မဟုတ် ဖုန်းနံပါတ် မပြည့်စုံသေးပါ။ ယဉ်ကျေးစွာ တောင်းခံပေးပါ။"

    final_prompt = f"""
    {knowledge_base}
    
    [IMPORTANT CONTEXT]
    {status_context}
    
    [USER LATEST MESSAGE]
    {user_message}
    
    အထက်ပါအချက်များကို အခြေခံ၍ လူကြီးမင်း၏ မေးခွန်းကို တိုတိုနှင့် ရှင်းရှင်းလင်းလင်း မြန်မာလို ပြန်ဖြေပေးပါ။
    """
    
    try:
        # History လုံးဝ မသုံးဘဲ လတ်တလောစာကိုပဲ ဖြေခိုင်းခြင်းဖြင့် Loop ပိတ်သည်
        response = model.generate_content(final_prompt)
        return response.text
    except:
        return "ခဏနေမှ ပြန်မေးပေးပါခင်ဗျာ။"

# ==========================================
# ၄။ WEBHOOK ROUTE
# ==========================================
@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        if request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge")
        return "Forbidden", 403
    
    if request.method == 'POST':
        body = request.json
        if body.get("object") == "page":
            for entry in body.get("entry", []):
                for event in entry.get("messaging", []):
                    if "message" in event and "text" in event["message"] and not event["message"].get("is_echo"):
                        sid = event["sender"]["id"]
                        txt = event["message"]["text"]
                        
                        # AI အဖြေကို Generate လုပ်သည်
                        reply = ask_gemini(sid, txt)
                        
                        # Facebook Messenger ဆီ တိုက်ရိုက်ပို့သည်
                        requests.post(f"https://graph.facebook.com/v12.0/me/messages?access_token={PAGE_ACCESS_TOKEN}", 
                                      json={"recipient": {"id": sid}, "message": {"text": reply}})
            return "OK", 200
    return "Not Found", 404

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
