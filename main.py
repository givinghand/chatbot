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
    
    # Gemini formatında geçmişi ekle
    data[str_chat_id].append({"role": "user", "parts": [user_msg]})
    data[str_chat_id].append({"role": "model", "parts": [bot_msg]})
    
    # Hafıza şişmesin, son 30 mesajı tutalım
    data[str_chat_id] = data[str_chat_id][-30:]
    
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# --- GEMINI AYARLARI (KOÇ MODU) ---
genai.configure(api_key=GEMINI_API_KEY)

system_instruction = """
Sen 'Ali K. Yazıcı'nın yapay zeka spor ve beslenme koçusun.
Adın: 'Beton Koç'.
Kişiliğin:
- Disiplinli, sert ama içten içe babacan.
- Hitapların: "Aslanım", "Hocam", "Şampiyon", "Evlat", "Kanka".
- Asla "Siz" diye konuşma, "Sen" diye konuş. Samimi ol.
- Kullanıcı yemek fotoğrafı atarsa: Kalorisi, yağı, şekeri hakkında yorum yap. Sağlıksızsa fırçayı bas.
- Kullanıcı antrenman veya vücut fotosu atarsa: Formunu yorumla, eksiklerini söyle.
- Hafızan var: Geçmişte ne konuştuğumuzu hatırla. Dün "bacak çalıştım" dediyse bugün "Bacaklar nasıl?" diye sor.
- Cevapların net ve kısa olsun, destan yazma.
"""

# EN GÜVENİLİR VE GÜÇLÜ MODEL: 1.5 PRO
model = genai.GenerativeModel(
    model_name='gemini-1.5-pro', 
    system_instruction=system_instruction
)

# --- YARDIMCI FONKSİYONLAR ---

def send_telegram_action(chat_id, action="typing"):
    """Yazıyor... efekti gönderir"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction"
        requests.post(url, json={"chat_id": chat_id, "action": action})
    except:
        pass

def send_telegram_message(chat_id, text):
    """Mesaj gönderir"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Mesaj gönderme hatası: {e}")

def get_file_content(file_id):
    """Telegram'dan Fotoğraf veya Ses dosyasını indirir"""
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
    
    # Boş istek veya mesajsız istek kontrolü
    if not data or "message" not in data:
        return "OK", 200

    chat_id = data["message"]["chat"]["id"]
    
    try:
        # 1. BOT İNSAN GİBİ BEKLESİN
        send_telegram_action(chat_id, "typing")
        # 4 ile 10 saniye arası rastgele bekleme (Gerçekçi olsun)
        time.sleep(random.randint(4, 10))

        # 2. İÇERİĞİ AL
        user_parts = []
        user_text_log = "" 

        # A) Fotoğraf Geldiyse
        if "photo" in data["message"]:
            file_id = data["message"]["photo"][-1]["file_id"]
            content, mime = get_file_content(file_id)
            if content:
                user_parts.append({"mime_type": mime, "data": content})
                user_text_log += "[Kullanıcı Fotoğraf Attı] "
            
            caption = data["message"].get("caption", "Hocam şu fotoğrafa bir bak, yorumla.")
            user_parts.append(caption)
            user_text_log += caption

        # B) Ses (Voice) Geldiyse
        elif "voice" in data["message"]:
            file_id = data["message"]["voice"]["file_id"]
            content, mime = get_file_content(file_id)
            if content:
                user_parts.append({"mime_type": mime, "data": content})
                user_parts.append("Bu ses kaydını dinle. Koç tavrıyla cevap ver.")
                user_text_log += "[Ses Kaydı] "

        # C) Sadece Yazı Geldiyse
        elif "text" in data["message"]:
            user_parts.append(data["message"]["text"])
            user_text_log += data["message"]["text"]

        else:
            # Sticker vs. gelirse cevap verme veya geç
            return "OK", 200 

        # 3. GEÇMİŞİ YÜKLE VE CEVAP AL
        history = load_memory().get(str(chat_id), [])
        
        chat = model.start_chat(history=history)
        response = chat.send_message(user_parts)
        bot_response = response.text

        # 4. CEVABI GÖNDER VE KAYDET
        send_telegram_message(chat_id, bot_response)
        save_memory(chat_id, user_text_log, bot_response)

    except Exception as e:
        print(f"Hata oluştu: {e}")
        # Kullanıcıya hata mesajı (Role uygun)
        send_telegram_message(chat_id, "Aslanım şu an salonda ağırlık basıyorum, mesajın arada kaynadı. Tekrar yazsana.")

    return "OK", 200

# --- GÜNLÜK KONTROL TETİKLEYİCİSİ ---
# cron-job.org buraya istek atacak
@app.route('/gunluk_kontrol', methods=['GET'])
def gunluk_kontrol():
    memory = load_memory()
    user_ids = memory.keys()
    
    mesajlar = [
        "Akşam oldu aslanım! Bugün ne yedin ne içtin? Rapor ver.",
        "İdman yapıldı mı? Yoksa kaytarıyor musun? Dürüst ol.",
        "Beton Koç kontrol saati! Makrolar ne durumda?",
        "Bugün hedefi tutturdun mu şampiyon? Fotoğraf veya ses bekliyorum."
    ]
    
    count = 0
    for chat_id in user_ids:
        try:
            msg = random.choice(mesajlar)
            send_telegram_message(chat_id, msg)
            count += 1
            time.sleep(2) # Arka arkaya mesaj atarken spam'e düşmeyelim
        except:
            continue
            
    return f"{count} kişiye dürtme mesajı atıldı.", 200

if __name__ == '__main__':
    # Render'da host 0.0.0.0 olmalı
    app.run(host='0.0.0.0', port=10000)
