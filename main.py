import os
import requests
from flask import Flask, request
import google.generativeai as genai

app = Flask(__name__)

# --- AYARLAR (Render'a gireceğiz) ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Gemini Ayarı
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

def send_telegram(chat_id, text):
    """Telegram'a mesaj gönderir"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    requests.post(url, json=payload)

def get_file_content(file_id):
    """Telegram'dan fotoğraf veya ses dosyasını indirir"""
    # 1. Dosya yolunu al
    get_path_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}"
    path_resp = requests.get(get_path_url).json()
    file_path = path_resp['result']['file_path']
    
    # 2. Dosyayı indir
    download_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    file_content = requests.get(download_url).content
    
    # MIME type belirle (Basitçe)
    mime_type = "image/jpeg" if "photos" in file_path else "audio/ogg"
    return file_content, mime_type

@app.route('/', methods=['POST']) # Telegram buraya post atar
def webhook():
    data = request.json
    
    if "message" not in data:
        return "OK", 200

    chat_id = data["message"]["chat"]["id"]
    
    # Yükleniyor mesajı verelim (Kullanıcı beklediğini bilsin)
    send_telegram(chat_id, "👷 İnceliyorum...")

    try:
        parts = [] # Gemini'ye gidecek paket
        prompt_text = "Sen uzman bir inşaat mühendisisin. Bu içeriği teknik olarak incele ve Türkçe yanıtla."

        # 1. TÜR: FOTOĞRAF
        if "photo" in data["message"]:
            # En yüksek çözünürlüğü al
            file_id = data["message"]["photo"][-1]["file_id"]
            content, mime = get_file_content(file_id)
            parts.append({"mime_type": mime, "data": content})
            
            # Resim altında yazı var mı?
            if "caption" in data["message"]:
                prompt_text = data["message"]["caption"]

        # 2. TÜR: SES (Voice Note)
        elif "voice" in data["message"]:
            file_id = data["message"]["voice"]["file_id"]
            content, mime = get_file_content(file_id)
            parts.append({"mime_type": mime, "data": content})
            prompt_text += " (Bu bir sesli not, söylenenleri dikkate alarak cevapla.)"

        # 3. TÜR: SADECE YAZI
        elif "text" in data["message"]:
            prompt_text = data["message"]["text"]

        # Gemini'ye Gönder
        parts.append(prompt_text)
        response = model.generate_content(parts)
        
        # Cevabı Telegram'a İlet
        send_telegram(chat_id, response.text)

    except Exception as e:
        send_telegram(chat_id, f"Hata oluştu: {str(e)}")

    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
