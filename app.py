import os
import json
import gspread
import requests
import time
from threading import Thread
from flask import Flask, request, jsonify
import google.generativeai as genai
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# ==========================================
# ၁။ Environment Variables
# ==========================================
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
SERVICE_ACCOUNT_JSON = os.environ.get('SERVICE_ACCOUNT_JSON')

# ==========================================
# ၂။ GEMINI SETUP (1.5 Flash)
# ==========================================
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-flash-latest')
    user_sessions = {} # ဒီအထဲမှာ User ပြောသမျှ မှတ်ထားပါမယ်
else:
    print("⚠️ Error: GOOGLE_API_KEY is missing!")

# ==========================================
# ၃။ GOOGLE SHEETS (NAME, PHONE, SERVICE)
# ==========================================
def save_to_google_sheet(sender_id, extracted_data):
    try:
        if not SERVICE_ACCOUNT_JSON: return

        service_info = json.loads(SERVICE_ACCOUNT_JSON)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(service_info, scope)
        client = gspread.authorize(creds)
        
        sheet = client.open("WorkSmart_Leads").sheet1
        
        # Data ၃ ခုလုံး ယူမယ်
        name = extracted_data.get('name', 'N/A')
        phone = extracted_data.get('phone', 'N/A')
        service = extracted_data.get('service', 'N/A')

        if name == 'N/A' and phone == 'N/A' and service == 'N/A':
            return

        # ID ရှာမယ်
        cell = sheet.find(str(sender_id), in_column=1)
        
        if cell:
            # လူဟောင်းဆိုရင် Update လုပ်မယ်
            row_number = cell.row
            if name != 'N/A': sheet.update_cell(row_number, 2, name)    # Col 2 = Name
            if phone != 'N/A': sheet.update_cell(row_number, 3, phone)   # Col 3 = Phone
            if service != 'N/A': sheet.update_cell(row_number, 4, service) # Col 4 = Service
            print(f"✅ Updated Lead: {name}")
        else:
            # လူသစ်ဆိုရင် အသစ်ထည့်မယ်
            sheet.append_row([str(sender_id), name, phone, service])
            print(f"✅ Added New Lead: {name}")
            
    except Exception as e:
        print(f"🔴 Google Sheet Error: {e}")

def check_and_extract_lead(sender_id):
    """
    စကားပြော History တစ်ခုလုံးကို ပြန်ဖတ်ပြီး နာမည်၊ ဖုန်း၊ Service ကို ရှာဖွေခြင်း
    """
    try:
        if sender_id not in user_sessions: return

        # History တစ်ခုလုံးကို စာပြန်စီမယ် (ဒါမှ အရင်ပြောတာတွေ မှတ်မိမှာ)
        chat_history = user_sessions[sender_id].history
        history_text = ""
        for message in chat_history:
            role = "User" if message.role == "user" else "Bot"
            history_text += f"{role}: {message.parts[0].text}\n"

        prompt = f"""
        Analyze the conversation history. Extract User's NAME, PHONE, and INTERESTED SERVICE.
        
        [CONTEXT]
        Services: "AI Content Course", "Auto Bot Service"
        
        [RULES]
        1. Extract NAME if user mentioned it (e.g., "I am Mg Mg").
        2. Extract PHONE (09..., +959...).
        3. Extract SERVICE they are interested in.
        4. If missing, use "N/A".
        5. Return JSON ONLY.
        
        History: 
        {history_text}
        
        Output Format: {{"name": "...", "phone": "...", "service": "..."}}
        """
        
        response = model.generate_content(prompt)
        
        if "{" in response.text:
            json_str = response.text.replace("```json", "").replace("```", "").strip()
            start = json_str.find('{')
            end = json_str.rfind('}') + 1
            lead_data = json.loads(json_str[start:end])
            
            save_to_google_sheet(sender_id, lead_data)
            
    except Exception as e:
        print(f"🔴 Extraction Error: {e}")

# ==========================================
# ၄။ BRAIN & INSTRUCTIONS (Memory & Persona)
# ==========================================
def ask_gemini(sender_id, message):
    try:
        if sender_id not in user_sessions:
            system_instruction = [
                {
                    "role": "user",
                    "parts": """
                    You are the Male Admin of 'Work Smart with AI'.
                    [YOUR PERSONA]
                    - Gender: Male (Use 'ကျွန်တော်' for I, 'ခင်ဗျာ' for polite ending).
                    - Tone: Professional, Helpful.
                    
                    [OUR SERVICES]
                    1. AI Content Writing Course.
                    2. Sales Enhancement Auto Bot Service.

                    [Rules]
                    1. User က လုပ်နည်းတွေမေးလာရင်မဖြေရဘူး။ သင်တန်းအပ်ပြီး လေ့လာဖို့ပဲပြောရမယ်။
                    2. Leads ရဖို့အဓိကပါ။
                    
                    
                    [GOAL]
                    - Answer questions.
                    - Politely ask for their NAME and PHONE Number to register/contact.
                    
                    [MEMORY]
                    - Remember what the user said previously in this conversation.
                    - If they already gave their name, use it to address them.
                    """
                },
                { "role": "model", "parts": "Acknowledged. I will remember user details." }
            ]
            # start_chat က History ကို Auto သိမ်းပေးပါတယ်
            user_sessions[sender_id] = model.start_chat(history=system_instruction)

        chat = user_sessions[sender_id]
        
        # Retry Logic
        for attempt in range(3):
            try:
                response = chat.send_message(message)
                return response.text
            except Exception as e:
                time.sleep(1)
                if attempt == 2: return "System Error ဖြစ်နေလို့ နောက်မှ ပြန်မေးပေးပါခင်ဗျာ။"

    except Exception as e:
        print(f"🔴 Gemini Error: {e}")
        return "System Error ဖြစ်နေပါသည်"

# ==========================================
# ၅။ ROUTES
# ==========================================
@app.route('/', methods=['GET'])
def home():
    return "Work Smart AI Bot (With Memory) is Ready!", 200

@app.route('/manychat', methods=['POST'])
def manychat_hook():
    try:
        data = request.json
        user_id = str(data.get('user_id'))
        user_message = data.get('message')
        
        bot_reply = ask_gemini(user_id, user_message)
        
        # Sheet ထဲ သိမ်းတာကို နောက်ကွယ်မှာ လုပ်မယ်
        thread = Thread(target=check_and_extract_lead, args=(user_id,))
        thread.start()
        
        return jsonify({"response": bot_reply}), 200
    except Exception as e:
        return jsonify({"response": "Error"}), 500

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
