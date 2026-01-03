import os
import time
import random
import json
import requests
from flask import Flask, request
import google.generativeai as genai

app = Flask(__name__)

# --- AYARLAR ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# --- KOÇUN HAFIZASI ---
MEMORY_FILE = "koc_hafizasi.json"

def load_memory():
    """Geçmiş konuşmaları dosyadan yükler"""
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_memory(chat_id, user_msg, bot_msg):
    """Konuşmayı dosyaya kaydeder"""
    data = load_memory()
    str_chat_id = str(chat_id)
    
    if str_chat_id not in data:
        data[str_chat_id] = []
    
    # Hafızaya ekle
    data[str_chat_id].append({"role": "user", "parts": [user_msg]})
    data[str_chat_id].append({"role": "model", "parts": [bot_msg]})
    
    # --- DEĞİŞİKLİK BURADA ---
    # Eskiden burada hafızayı siliyorduk ([-30:]).
    # Artık silmiyoruz. Gemini 2.5 Flash her şeyi aklında tutabilir.
    # Sınırsız hafıza modu aktif.
    
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# --- GEMINI AYARLARI (KOÇ MODU) ---
genai.configure(api_key=GEMINI_API_KEY)

system_instruction = """
Sen 'Ali K. Yazıcı'nın yapay zeka spor ve beslenme koçusun.
Adın: 'Beton Koç'.
Kişiliğin:
- Disiplinli, otoriter ama samimi (Babacan sertlik).
- Hitapların: "Aslanım", "Hocam", "Şampiyon", "Evlat", "Kanka".
- Asla "Siz" diye konuşma, "Sen" diye konuş.
- Kullanıcı yemek fotoğrafı atarsa: Kalorisi, besin değeri hakkında yorum yap. Sağlıksızsa fırçayı bas.
- Kullanıcı antrenman/vücut fotosu atarsa: Formunu yorumla, eksiklerini söyle.
- KESİN KURAL: Hafızan çok güçlü. Kullanıcının sana aylar önce söylediği sakatlıkları, sevmediği yemekleri asla unutma.
- Cevapların net ve kısa olsun.
"""

# Güçlü ve Geniş Hafızalı Model: 2.5 Flash
model = genai.GenerativeModel(
    model_name='gemini-2.5-flash', 
    system_instruction=system_instruction
)

# --- YARDIMCI FONKSİYONLAR ---

def send_telegram_action(chat_id, action="typing"):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction"
        requests.post(url, json={"chat_id": chat_id, "action": action})
    except:
        pass

def send_telegram_message(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Mesaj gönderme hatası: {e}")

def get_file_content(file_id):
    try:
        path_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}"
        path_resp = requests.get(path_url).json()
        
        if not path_resp.get('ok'):
            return None, None

        file_path = path_resp['result']['file_path']
        download_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
        file_content = requests.get(download_url).content
        
        mime_type = "image/jpeg" if "photos" in file_path else "audio/ogg"
        return file_content, mime_type
    except:
        return None, None

# --- ANA ENDPOINTLER ---

@app.route('/', methods=['POST'])
def webhook():
    data = request.json
    
    if not data or "message" not in data:
        return "OK", 200

    chat_id = data["message"]["chat"]["id"]
    
    try:
        # 1. Bekleme Efekti
        send_telegram_action(chat_id, "typing")
        time.sleep(random.randint(2, 5))

        # 2. İçeriği Al
        user_parts = []
        user_text_log = "" 

        if "photo" in data["message"]:
            file_id = data["message"]["photo"][-1]["file_id"]
            content, mime = get_file_content(file_id)
            if content:
                user_parts.append({"mime_type": mime, "data": content})
                user_text_log += "[Kullanıcı Fotoğraf Attı] "
            caption = data["message"].get("caption", "Hocam fotoğrafa bak.")
            user_parts.append(caption)
            user_text_log += caption

        elif "voice" in data["message"]:
            file_id = data["message"]["voice"]["file_id"]
            content, mime = get_file_content(file_id)
            if content:
                user_parts.append({"mime_type": mime, "data": content})
                user_parts.append("Bu ses kaydını dinle.")
                user_text_log += "[Ses Kaydı] "

        elif "text" in data["message"]:
            user_parts.append(data["message"]["text"])
            user_text_log += data["message"]["text"]

        else:
            return "OK", 200 

        # 3. TÜM HAFIZAYI ÇAĞIR
        history = load_memory().get(str(chat_id), [])
        
        chat = model.start_chat(history=history)
        response = chat.send_message(user_parts)
        bot_response = response.text

        # 4. GÖNDER & KAYDET
        send_telegram_message(chat_id, bot_response)
        save_memory(chat_id, user_text_log, bot_response)

    except Exception as e:
        print(f"Hata: {e}")
        send_telegram_message(chat_id, "Bağlantı koptu aslanım, tekrar yaz.")

    return "OK", 200

# --- GÜNLÜK KONTROL ---
@app.route('/gunluk_kontrol', methods=['GET'])
def gunluk_kontrol():
    memory = load_memory()
    user_ids = memory.keys()
    
    mesajlar = [
        "Akşam raporu ver aslanım! Bugün ne yedin?",
        "İdman yapıldı mı? Dürüst ol.",
        "Hedefe odaklan şampiyon. Bugün kaçamak var mı?",
        "Beton Koç dinliyor. Günün nasıl geçti?"
    ]
    
    count = 0
    for chat_id in user_ids:
        try:
            msg = random.choice(mesajlar)
            send_telegram_message(chat_id, msg)
            count += 1
            time.sleep(2)
        except:
            continue
            
    return f"{count} kişiye mesaj atıldı.", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
