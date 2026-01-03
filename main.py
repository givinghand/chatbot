import os
import time
import random
import json
import base64
import requests
import threading
from flask import Flask, request
import google.generativeai as genai

app = Flask(__name__)

# ==============================================================================
# AYARLAR
# ==============================================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

# GitHub Link Temizliği
GITHUB_REPO_RAW = os.environ.get("GITHUB_REPO", "")
GITHUB_REPO = GITHUB_REPO_RAW.replace("https://github.com/", "").strip("/")
GITHUB_FILE_PATH = "koc_hafizasi.json"

# BEKLEME SÜRESİ (Saniye)
# Kullanıcı yazmayı bıraktıktan kaç saniye sonra cevap verilsin?
# 15-20 saniye idealdir. 60 saniye çok uzun gelebilir ama burayı değiştirebilirsin.
WAIT_TIME = 30 

# Kullanıcıların mesajlarını geçici tuttuğumuz tampon bellek
# Yapı: { "chat_id": { "parts": [], "logs": "", "timer": <Thread> } }
user_buffers = {}

# ==============================================================================
# GITHUB & HAFIZA FONKSİYONLARI
# ==============================================================================

def get_github_file():
    if not GITHUB_TOKEN or not GITHUB_REPO: return {}, None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            content_b64 = response.json()['content']
            content_json = base64.b64decode(content_b64).decode('utf-8')
            sha = response.json()['sha']
            return json.loads(content_json), sha
        return {}, None
    except: return {}, None

def update_github_file(data, sha):
    if not GITHUB_TOKEN or not GITHUB_REPO: return False
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    
    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    content_b64 = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
    payload = {"message": "Beton Koç Hafıza Update", "content": content_b64}
    if sha: payload["sha"] = sha

    try:
        r = requests.put(url, headers=headers, json=payload)
        return r.status_code in [200, 201]
    except: return False

def load_memory():
    data, _ = get_github_file()
    return data

def save_memory(chat_id, user_msg, bot_msg):
    # Retry mekanizması
    max_retries = 3
    str_chat_id = str(chat_id)
    for attempt in range(max_retries):
        try:
            data, sha = get_github_file()
            if str_chat_id not in data: data[str_chat_id] = []
            
            data[str_chat_id].append({"role": "user", "parts": [user_msg]})
            data[str_chat_id].append({"role": "model", "parts": [bot_msg]})
            
            if len(data[str_chat_id]) > 200:
                data[str_chat_id] = data[str_chat_id][-200:]

            if update_github_file(data, sha): break
            time.sleep(1)
        except: time.sleep(1)

# ==============================================================================
# AI & TELEGRAM AYARLARI
# ==============================================================================
genai.configure(api_key=GEMINI_API_KEY)
system_instruction = """
Sen profesyonel bir yapay zeka spor ve beslenme koçusun. Adın: 'Beton Koç'.
KİŞİLİK: Disiplinli, otoriter ama babacan. "Aslanım", "Hocam", "Şampiyon" de. "Siz" deme.
GÖREV: Gelen TÜM fotoğrafları ve metinleri tek bir bağlamda değerlendir.
Eğer 3-4 yemek fotosu geldiyse hepsini topla, genel bir yorum yap.
"""
model = genai.GenerativeModel(model_name='gemini-1.5-flash', system_instruction=system_instruction)

def send_telegram_action(chat_id, action="typing"):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction", json={"chat_id": chat_id, "action": action})
    except: pass

def send_telegram_message(chat_id, text):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text})
    except: pass

def get_file_content(file_id):
    try:
        path_resp = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}").json()
        if not path_resp.get('ok'): return None, None
        file_path = path_resp['result']['file_path']
        content = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}").content
        mime = "image/jpeg" if "photos" in file_path else "audio/ogg"
        return content, mime
    except: return None, None

# ==============================================================================
# ARKA PLAN İŞLEMCİSİ (HEPSİNİ TOPLAYIP CEVAP VEREN KISIM)
# ==============================================================================

def process_accumulated_messages(chat_id):
    """Süre dolunca çalışır. Tampondaki her şeyi paketleyip Gemini'ye yollar."""
    try:
        if chat_id not in user_buffers: return

        # Tampondaki verileri al
        buffer_data = user_buffers.pop(chat_id) # Veriyi al ve tamponu temizle
        parts = buffer_data['parts']
        text_log = buffer_data['logs']

        # Kullanıcıya "İşliyorum..." sinyali ver
        send_telegram_action(chat_id, "typing")

        # Hafızayı ve Modeli Çağır
        history = load_memory().get(str(chat_id), [])
        chat = model.start_chat(history=history)
        
        # Gemini'ye tek seferde gönder
        response = chat.send_message(parts)
        bot_response = response.text

        # Cevabı Telegram'a yaz
        send_telegram_message(chat_id, bot_response)
        
        # Hafızaya tek parça olarak kaydet
        save_memory(chat_id, text_log, bot_response)

    except Exception as e:
        print(f"İşleme Hatası: {e}")
        send_telegram_message(chat_id, "Aslanım kafam karıştı, tekrar dener misin?")

# ==============================================================================
# WEBHOOK (Artık Sadece Veri Topluyor)
# ==============================================================================

@app.route('/', methods=['POST'])
def webhook():
    data = request.json
    if not data or "message" not in data: return "OK", 200

    chat_id = data["message"]["chat"]["id"]
    
    # 1. Kullanıcı tamponunu oluştur (Yoksa)
    if chat_id not in user_buffers:
        user_buffers[chat_id] = {
            "parts": [],
            "logs": "",
            "timer": None
        }

    # 2. Varsa eski sayacı iptal et (Debounce Mantığı)
    if user_buffers[chat_id]["timer"]:
        user_buffers[chat_id]["timer"].cancel()

    # 3. Gelen veriyi tampona ekle
    # A) Fotoğraf
    if "photo" in data["message"]:
        file_id = data["message"]["photo"][-1]["file_id"]
        content, mime = get_file_content(file_id)
        if content:
            user_buffers[chat_id]["parts"].append({"mime_type": mime, "data": content})
            user_buffers[chat_id]["logs"] += "[Foto] "
        
        caption = data["message"].get("caption", "")
        if caption:
            user_buffers[chat_id]["parts"].append(caption)
            user_buffers[chat_id]["logs"] += caption + " "

    # B) Ses
    elif "voice" in data["message"]:
        file_id = data["message"]["voice"]["file_id"]
        content, mime = get_file_content(file_id)
        if content:
            user_buffers[chat_id]["parts"].append({"mime_type": mime, "data": content})
            user_buffers[chat_id]["parts"].append("Bu ses kaydını dinle.")
            user_buffers[chat_id]["logs"] += "[Ses] "

    # C) Metin
    elif "text" in data["message"]:
        text = data["message"]["text"]
        user_buffers[chat_id]["parts"].append(text)
        user_buffers[chat_id]["logs"] += text + " "

    # 4. Yeni Sayaç Başlat (Arka planda çalışır)
    # WAIT_TIME kadar bekler, araya başka mesaj girmezse process_accumulated_messages çalışır.
    timer = threading.Timer(WAIT_TIME, process_accumulated_messages, args=[chat_id])
    user_buffers[chat_id]["timer"] = timer
    timer.start()

    # Telegram'a hemen "Tamam" de ki hata vermesin
    return "...", 200

# ==============================================================================
# GÜNLÜK KONTROL
# ==============================================================================
@app.route('/gunluk_kontrol', methods=['GET'])
def gunluk_kontrol():
    memory = load_memory()
    mesajlar = ["Akşam raporu ver aslanım!", "İdman yapıldı mı?", "Bugün kaçamak var mı?"]
    count = 0
    for chat_id in memory.keys():
        try:
            send_telegram_message(chat_id, random.choice(mesajlar))
            count += 1
            time.sleep(2)
        except: continue
    return f"{count} kişiye mesaj atıldı.", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
