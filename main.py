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
    """Geçmiş konuşmaları yükler"""
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
    
    # Hafızaya ekle (Gemini formatında)
    data[str_chat_id].append({"role": "user", "parts": [user_msg]})
    data[str_chat_id].append({"role": "model", "parts": [bot_msg]})
    
    # Hafıza çok şişmesin, son 30 mesajı tutalım
    data[str_chat_id] = data[str_chat_id][-30:]
    
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# --- GEMINI AYARLARI (KOÇ MODU) ---
genai.configure(api_key=GEMINI_API_KEY)

# Burası Koçun Beyni (System Instruction)
system_instruction = """
Sen Ali Hocam'ın yapay zeka spor ve beslenme koçusun.
İsmin: 'Beton Koç'.
Kişiliğin: 
- Sert, disiplinli ama motive edici. Askeri disiplin ile abi şefkati arasında ol.
- "Aslanım", "Kanka", "Hocam", "Şampiyon", "Evlat" gibi hitaplar kullan.
- Asla resmi konuşma. "Siz" deme, "Sen" de.
- Kullanıcı yemek fotoğrafı atarsa, besin değerini analiz et. Eğer sağlıksızsa fırçayı bas.
- Kullanıcı antrenman/vücut fotoğrafı atarsa formunu yorumla, eksiklerini söyle.
- Geçmiş konuşmaları MUTLAKA hatırla. Dün ne yediğini, ne çalıştığını sor.
- Kısa ve öz konuş, destan yazma. Net ol.
"""

# DİKKAT: Ekran görüntüsündeki MODEL ID'yi girdim.
# Eğer API key'in henüz 3.0 için yetkili değilse hata verebilir.
# Öyle bir durumda burayı tekrar 'gemini-1.5-pro' yapman gerekir.
try:
    model = genai.GenerativeModel(
        model_name='gemini-3-pro-preview', 
        system_instruction=system_instruction
    )
except:
    # Eğer 3.0 hata verirse otomatik olarak 1.5 Pro'ya düşsün (Yedek Plan)
    print("3.0 Modeli bulunamadı, 1.5 Pro kullanılıyor...")
    model = genai.GenerativeModel(
        model_name='gemini-1.5-pro', 
        system_instruction=system_instruction
    )

# --- YARDIMCI FONKSİYONLAR ---

def send_telegram_action(chat_id, action="typing"):
    """Yazıyor... efekti verir"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction"
    requests.post(url, json={"chat_id": chat_id, "action": action})

def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    requests.post(url, json=payload)

def get_file_content(file_id):
    """Telegram'dan fotoğraf/ses indirir"""
    path_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}"
    path_resp = requests.get(path_url).json()
    
    if not path_resp.get('ok'):
        return None, None

    file_path = path_resp['result']['file_path']
    download_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    file_content = requests.get(download_url).content
    
    mime_type = "image/jpeg" if "photos" in file_path else "audio/ogg"
    return file_content, mime_type

# --- ANA ENDPOINTLER ---

@app.route('/', methods=['POST'])
def webhook():
    data = request.json
    
    # Mesaj kontrolü
    if "message" not in data:
        return "OK", 200

    chat_id = data["message"]["chat"]["id"]
    
    try:
        # 1. BOT İNSAN GİBİ BEKLESİN
        send_telegram_action(chat_id, "typing")
        # 3.0 çok hızlıdır ama biz yine de cool görünsün diye 4-8 saniye bekletelim
        time.sleep(random.randint(4, 8))

        # 2. İÇERİĞİ AL
        user_parts = []
        user_text_log = "" 

        # Fotoğraf İşleme
        if "photo" in data["message"]:
            file_id = data["message"]["photo"][-1]["file_id"]
            content, mime = get_file_content(file_id)
            if content:
                user_parts.append({"mime_type": mime, "data": content})
                user_text_log += "[Kullanıcı Fotoğraf Attı] "
            
            caption = data["message"].get("caption", "Hocam şu fotoğrafa bir bak, yorumla.")
            user_parts.append(caption)
            user_text_log += caption

        # Ses İşleme
        elif "voice" in data["message"]:
            file_id = data["message"]["voice"]["file_id"]
            content, mime = get_file_content(file_id)
            if content:
                user_parts.append({"mime_type": mime, "data": content})
                user_parts.append("Bu ses kaydını dinle. Kullanıcının dediklerine koç tavrıyla cevap ver.")
                user_text_log += "[Ses Kaydı]"

        # Metin İşleme
        elif "text" in data["message"]:
            user_parts.append(data["message"]["text"])
            user_text_log += data["message"]["text"]

        else:
            return "OK", 200 

        # 3. HAFIZAYI ÇAĞIR VE CEVAP ÜRET
        history = load_memory().get(str(chat_id), [])
        
        chat = model.start_chat(history=history)
        response = chat.send_message(user_parts)
        bot_response = response.text

        # 4. CEVABI İLET VE KAYDET
        send_telegram_message(chat_id, bot_response)
        save_memory(chat_id, user_text_log, bot_response)

    except Exception as e:
        print(f"Hata: {e}")
        # Hata mesajını biraz daha yumuşatalım
        send_telegram_message(chat_id, "Aslanım hatlar karıştı, sesin gelmedi. Bir daha yazsana.")

    return "OK", 200

# --- GÜNLÜK DÜRTME MEKANİZMASI ---
@app.route('/gunluk_kontrol', methods=['GET'])
def gunluk_kontrol():
    memory = load_memory()
    user_ids = memory.keys()
    
    sorular = [
        "Hocam akşam oldu! Bugün boğazını tutabildin mi? Dökül bakalım.",
        "Antrenman yapıldı mı aslanım? Yoksa yine bahane mi ürettin?",
        "Bugünkü protein hedefini tutturdun mu? Rapor ver.",
        "Beton Koç kontrol saati! Bugün kaçamak var mı? Fotoğraf veya ses at.",
        "Günün nasıl geçti şampiyon? Beslenme ve idman detaylarını bekliyorum."
    ]
    
    count = 0
    for chat_id in user_ids:
        try:
            msg = random.choice(sorular)
            send_telegram_message(chat_id, msg)
            count += 1
            time.sleep(2)
        except:
            continue
            
    return f"{count} kişiye mesaj atıldı.", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
