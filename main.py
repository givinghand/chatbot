import os
import requests
from flask import Flask, request
import google.generativeai as genai

app = Flask(__name__)

# --- AYARLAR (Render Environment Variables'dan gelecek) ---
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "ali_insaat_bot")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Gemini Kurulumu
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

def get_image_bytes(media_id):
    """WhatsApp'tan fotoğrafı indirir"""
    url = f"https://graph.facebook.com/v18.0/{media_id}"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    
    # 1. Medya URL'sini al
    response = requests.get(url, headers=headers)
    image_url = response.json().get('url')
    
    # 2. Görüntüyü indir
    image_response = requests.get(image_url, headers=headers)
    return image_response.content

def send_whatsapp_message(to_number, text, phone_id):
    """Kullanıcıya cevap yazar"""
    url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "text": {"body": text}
    }
    requests.post(url, json=payload, headers=headers)

@app.route('/webhook', methods=['GET'])
def verify():
    """Meta'nın bizi doğrulaması için"""
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Hata: Token Yanlış", 403

@app.route('/webhook', methods=['POST'])
def webhook():
    """Mesaj geldiğinde çalışacak ana fonksiyon"""
    data = request.json
    try:
        # Gelen veriyi kontrol et
        if data.get('entry') and data['entry'][0]['changes'][0]['value'].get('messages'):
            message = data['entry'][0]['changes'][0]['value']['messages'][0]
            phone_id = data['entry'][0]['changes'][0]['value']['metadata']['phone_number_id']
            from_number = message['from']
            msg_type = message['type']
            
            response_text = "Anlaşılamadı."

            # SENARYO 1: Sadece Yazı Geldiyse
            if msg_type == 'text':
                user_text = message['text']['body']
                chat = model.start_chat(history=[])
                response = chat.send_message(user_text)
                response_text = response.text

            # SENARYO 2: Fotoğraf Geldiyse
            elif msg_type == 'image':
                media_id = message['image']['id']
                caption = message['image'].get('caption', "Bu görseli analiz et.") # Varsa resim altı yazısı
                
                # Resmi indir
                image_data = get_image_bytes(media_id)
                
                # Gemini'ye gönder (Görsel + Prompt)
                image_parts = [{"mime_type": "image/jpeg", "data": image_data}]
                prompt_parts = [caption, image_parts[0]]
                
                response = model.generate_content(prompt_parts)
                response_text = response.text

            # Cevabı Gönder
            send_whatsapp_message(from_number, response_text, phone_id)

    except Exception as e:
        print(f"Bir hata oluştu: {e}")
    
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
