import os
import json
import time
import gspread
import requests
import re
from threading import Thread
from flask import Flask, request, jsonify
import google.generativeai as genai
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# ==========================================
# ၁။ Environment Variables
# ==========================================
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
SERVICE_ACCOUNT_JSON = os.environ.get('SERVICE_ACCOUNT_JSON')

# ==========================================
# ၂။ GEMINI SETUP (1.5 Flash - Best for Speed/Cost)
# ==========================================
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-flash-latest')
    user_sessions = {} 
else:
    print("⚠️ Error: GOOGLE_API_KEY is missing!")

# ==========================================
# ၃။ GOOGLE SHEETS FUNCTIONS (ID, NAME, PHONE, SERVICE)
# ==========================================
def save_to_google_sheet(sender_id, extracted_data):
    try:
        if not SERVICE_ACCOUNT_JSON: return

        service_info = json.loads(SERVICE_ACCOUNT_JSON)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(service_info, scope)
        client = gspread.authorize(creds)
        
        # Sheet နာမည် မှန်ပါစေ "WorkSmart_Leads"
        try:
            sheet = client.open("WorkSmart_Leads").sheet1
        except:
            print("🔴 Error: 'WorkSmart_Leads' Sheet not found!")
            return
        
        # Data သန့်ရှင်းရေး
        name = extracted_data.get('name', 'N/A')
        phone = extracted_data.get('phone', 'N/A')
        service = extracted_data.get('service', 'N/A')

        # Debug Print (Render Log မှာ ကြည့်ဖို့)
        print(f"📝 Saving -> Name: {name}, Phone: {phone}, Service: {service}")

        # ဘာ Data မှ မပါရင် Sheet ထဲ မထည့်ဘူး
        if name in ['N/A', 'None'] and phone in ['N/A', 'None'] and service in ['N/A', 'None']:
            return

        # ID ရှာမယ် (Column 1)
        cell = sheet.find(str(sender_id), in_column=1)
        
        if cell:
            # လူဟောင်း (Update)
            row_number = cell.row
            if name not in ['N/A', 'None']: sheet.update_cell(row_number, 2, name)
            if phone not in ['N/A', 'None']: sheet.update_cell(row_number, 3, phone)
            if service not in ['N/A', 'None']: sheet.update_cell(row_number, 4, service)
            print(f"✅ Updated Row {row_number}")
        else:
            # လူသစ် (Append)
            sheet.append_row([str(sender_id), name, phone, service])
            print(f"✅ Created NEW Row for {name}")
            
    except Exception as e:
        print(f"🔴 Google Sheet Error: {e}")

def check_and_extract_lead(sender_id, current_message):
    try:
        # History ပြန်ကောက်မယ်
        history_text = ""
        if sender_id in user_sessions:
            for msg in user_sessions[sender_id].history:
                role = "User" if msg.role == "user" else "Bot"
                history_text += f"{role}: {msg.parts[0].text}\n"
        
        history_text += f"User (Latest): {current_message}\n"

        # Extraction Prompt (Service ပါ ထည့်ဆွဲမယ်)
        prompt = f"""
        Analyze the conversation history. Extract User's NAME, PHONE, and INTERESTED SERVICE.
        
        [CONTEXT - SERVICES]
        1. "AI Content Course" (Writing, Content Creation)
        2. "Auto Bot Service" (Chatbot, Reply, Automation)
        
        [RULES]
        1. Look closely at the "User (Latest)" message.
        2. Extract NAME if user mentioned it.
        3. Extract PHONE (Format: 09xxxxxxxxx).
        4. Match user interest to one of the SERVICES above.
        5. If info is missing, use "N/A".
        6. Return JSON ONLY.
        
        History:
        {history_text}
        
        Output JSON format: {{"name": "...", "phone": "...", "service": "..."}}
        """
        
        response = model.generate_content(prompt)
        text_response = response.text.strip()
        
        # JSON Cleaning (Code Block ဖယ်ရှားခြင်း)
        if "```json" in text_response:
            text_response = text_response.replace("```json", "").replace("```", "")
        elif "```" in text_response:
            text_response = text_response.replace("```", "")
            
        # JSON ရှာဖွေခြင်း (Regex - အတိကျဆုံးနည်း)
        json_match = re.search(r'\{.*\}', text_response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            lead_data = json.loads(json_str)
            save_to_google_sheet(sender_id, lead_data)
        else:
            print("⚠️ No valid JSON found in extraction")
            
    except Exception as e:
        print(f"🔴 Extraction Error: {e}")

# ==========================================
# ၄။ BRAIN & INSTRUCTIONS (Work Smart Persona)
# ==========================================
def ask_gemini(sender_id, message):
    if sender_id not in user_sessions:
        # System Instruction (Loop မဖြစ်အောင် တားဆီးခြင်း)
        system_instruction = [
            {
                "role": "user",
                "parts": """
                You are the Male Admin (ကျွန်တော်) of 'Work Smart with AI'.
                
                [OUR SERVICES]
                1. AI Content Writing Course.
                2. Sales Enhancement Auto Bot Service.
                
                [YOUR GOAL]
                - Answer questions professionally.
                - Collect NAME and PHONE Number to register/contact.
                
                [CRITICAL RULES - DO NOT BREAK]
                1. NO HOW-TOs: If user asks "How to do X?", do NOT teach them. Say "အသေးစိတ်သင်ယူဖို့ ကျွန်တော်တို့ သင်တန်းရှိပါတယ်ခင်ဗျာ".
                2. NO LOOPING: Check history! If user JUST gave Name/Phone, DO NOT ASK AGAIN. Say "လက်ခံရရှိပါတယ်".
                3. STOP ASKING: Once you have Name and Phone, stop pestering.
                4. SHORT ANSWERS: Keep replies under 3 sentences.
                """
            },
            { "role": "model", "parts": "Understood. I will act as the Male Admin, focus on sales, and stop asking once data is received." }
        ]
        user_sessions[sender_id] = model.start_chat(history=system_instruction)

    chat = user_sessions[sender_id]
    
    # Retry Logic (Connection ကျရင် ပြန်ကြိုးစားမယ်)
    for attempt in range(3):
        try:
            response = chat.send_message(message)
            return response.text
        except Exception as e:
            print(f"⚠️ Gemini Error (Attempt {attempt+1}): {e}")
            time.sleep(1)
            if attempt == 2: return "System Error ဖြစ်နေလို့ နောက် ၅ မိနစ်လောက်နေမှ ပြန်မေးပေးပါခင်ဗျာ။"

# ==========================================
# ၅။ ROUTES
# ==========================================
@app.route('/', methods=['GET'])
def home_status():
    return "Work Smart AI Bot is Perfect & Online!", 200

# Facebook Webhook
@app.route('/webhook', methods=['GET', 'POST'])
def fb_webhook_main():
    if request.method == 'GET':
        if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge")
        return "Verification Failed", 403

    if request.method == 'POST':
        try:
            body = request.json
            if body.get("object") == "page":
                for entry in body.get("entry", []):
                    for event in entry.get("messaging", []):
                        if "message" in event and "text" in event["message"] and not event["message"].get("is_echo"):
                            sender_id = event["sender"]["id"]
                            user_text = event["message"]["text"]
                            
                            # Logic ကို Thread နဲ့ ခွဲrun (Facebook Timeout မဖြစ်အောင်)
                            def handle_async():
                                check_and_extract_lead(sender_id, user_text) # Data အရင်ထုတ်
                                reply = ask_gemini(sender_id, user_text)     # ပြီးမှ စာပြန်
                                # ဒီနေရာမှာ Facebook ပြန်ပို့တဲ့ function လိုရင် ထည့်ပါ (ManyChat သုံးရင် မလိုပါ)
                                send_facebook_message(sender_id, reply)

                            thread = Thread(target=handle_async)
                            thread.start()
                            
                return "EVENT_RECEIVED", 200
        except Exception as e:
            print(f"🔴 Webhook Handling Error: {e}")
            return "ERROR", 500
    return "Not Found", 404

# ManyChat Hook (မိတ်ဆွေ ကုဒ်အဟောင်းအတိုင်း)
@app.route('/manychat', methods=['POST'])
def manychat_hook():
    try:
        data = request.json
        user_id = str(data.get('user_id'))
        user_message = data.get('message')
        
        # ၁။ Data အရင်ထုတ် (Background)
        thread = Thread(target=check_and_extract_lead, args=(user_id, user_message))
        thread.start()
        
        # ၂။ စာပြန်
        bot_reply = ask_gemini(user_id, user_message)
        
        return jsonify({"response": bot_reply}), 200
    except Exception as e:
        print(f"ManyChat Error: {e}")
        return jsonify({"response": "Error"}), 500

# Helper to send message back to FB (if not using ManyChat)
def send_facebook_message(recipient_id, text):
    if not PAGE_ACCESS_TOKEN: return
    url = f"[https://graph.facebook.com/v12.0/me/messages?access_token=](https://graph.facebook.com/v12.0/me/messages?access_token=){PAGE_ACCESS_TOKEN}"
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    headers = {"Content-Type": "application/json"}
    try:
        requests.post(url, json=payload, headers=headers)
    except Exception as e:
        print(f"🔴 FB Message Send Error: {e}")

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
