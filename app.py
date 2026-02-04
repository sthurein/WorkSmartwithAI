import os
import json
import gspread
import requests
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
# ၂။ GEMINI SETUP
# ==========================================
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    
    safety_settings = [
        { "category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE" },
        { "category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE" },
        { "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE" },
        { "category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE" },
    ]
    
    generation_config = {
        "temperature": 0.3,
        "top_p": 0.9,
        "top_k": 40,
        "max_output_tokens": 150,
    }
    
    model = genai.GenerativeModel(
        model_name='gemini-flash-latest', 
        safety_settings=safety_settings,
        generation_config=generation_config
    )
    
    user_sessions = {} 
else:
    print("⚠️ Error: GOOGLE_API_KEY is missing!")

# ==========================================
# ၃။ GOOGLE SHEETS
# ==========================================
def save_to_google_sheet(sender_id, extracted_data):
    try:
        if not SERVICE_ACCOUNT_JSON: return

        service_info = json.loads(SERVICE_ACCOUNT_JSON)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(service_info, scope)
        client = gspread.authorize(creds)
        
        sheet = client.open("WorkSmart_Leads").sheet1
        
        name = extracted_data.get('name', 'N/A')
        phone = extracted_data.get('phone', 'N/A')
        service = extracted_data.get('service', 'N/A')

        if name == 'N/A' and phone == 'N/A' and service == 'N/A':
            return

        cell = sheet.find(str(sender_id), in_column=1)
        
        if cell:
            row_number = cell.row
            if name != 'N/A': sheet.update_cell(row_number, 2, name)
            if phone != 'N/A': sheet.update_cell(row_number, 3, phone)
            if service != 'N/A': sheet.update_cell(row_number, 4, service)
            print(f"✅ Updated Client {name}")
        else:
            sheet.append_row([str(sender_id), name, phone, service])
            print(f"✅ Added New Client {name}")
            
    except Exception as e:
        print(f"🔴 Google Sheet Error: {e}")

def check_and_extract_lead(sender_id):
    try:
        if sender_id not in user_sessions: return

        chat_history = user_sessions[sender_id].history
        history_text = ""
        for message in chat_history:
            role = "User" if message.role == "user" else "Bot"
            history_text += f"{role}: {message.parts[0].text}\n"

        prompt = f"""
        Analyze conversation. Extract User's Name, Phone, and Interested Service.
        Context: 'Work Smart with AI' page. Services: AI Training, Chatbot Dev, Automation.
        RULES: Use LATEST info. If missing, use "N/A". Return JSON ONLY.
        History: {history_text}
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
# ၄။ BRAIN & INSTRUCTIONS
# ==========================================
def ask_gemini(sender_id, message):
    try:
        if sender_id not in user_sessions:
            system_instruction = [
                {
                    "role": "user",
                    "parts": """
                    You are the AI Assistant for 'Work Smart with AI'.
                    [YOUR ROLE] Professional, Helpful, Tech-savvy. Language: Burmese (Myanmar).
                    [SERVICES] 1. AI Training 2. Chatbot Development 3. Business Automation.
                    [RULES]
                    1. ONLY answer questions related to AI and Our Services.
                    2. Try to get their Phone Number for the Waitlist.
                    3. Keep answers short (Max 3 sentences).
                    """
                },
                { "role": "model", "parts": "Acknowledged." }
            ]
            user_sessions[sender_id] = model.start_chat(history=system_instruction)

        chat = user_sessions[sender_id]
        response = chat.send_message(message)
        return response.text
    except Exception as e:
        print(f"🔴 Gemini Error: {e}")
        return "ခဏနေမှ ပြန်မေးပေးပါခင်ဗျာ။"

# ==========================================
# ၅။ MANYCHAT ROUTE
# ==========================================
@app.route('/', methods=['GET'])
def home():
    return "Work Smart AI Bot is Ready!", 200

@app.route('/manychat', methods=['POST'])
def manychat_hook():
    try:
        data = request.json
        user_id = str(data.get('user_id'))
        user_message = data.get('message')
        
        bot_reply = ask_gemini(user_id, user_message)
        thread = Thread(target=check_and_extract_lead, args=(user_id,))
        thread.start()
        
        return jsonify({"response": bot_reply}), 200
    except Exception as e:
        return jsonify({"response": "Error"}), 500

if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
