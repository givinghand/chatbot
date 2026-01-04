import os
import time
import random
import json
import base64
import requests
import threading
from flask import Flask, request
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable, GoogleAPICallError

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
# Sen mesaj atmayı kestikten kaç saniye sonra cevap versin?
WAIT_TIME = 20 

# Kullanıcı Tampon Belleği
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
    str_chat_id = str(chat_id)
    # Retry (Tekrar deneme) mekanizması
    for attempt in range(3):
        try:
            data, sha = get_github_file()
            if str_chat_id not in data: data[str_chat_id] = []
            
            data[str_chat_id].append({"role": "user", "parts": [user_msg]})
            data[str_chat_id].append({"role": "model", "parts": [bot_msg]})
            
            # Hafıza çok şişerse son 200 mesajı tut
            if len(data[str_chat_id]) > 200:
                data[str_chat_id] = data[str_chat_id][-200:]

            if update_github_file(data, sha): break
            time.sleep(1)
        except: time.sleep(1)

# ==============================================================================
# AI AYARLARI (Web Search + Babacan Mod)
# ==============================================================================
genai.configure(api_key=GEMINI_API_KEY)

system_instruction = """
Sen "Beton Koç" lakaplı, profesyonel ama babacan bir yapay zeka spor ve beslenme koçusun.

KİŞİLİK VE ÜSLUP:
- 👊 **Babacan ve Otoriter:** "Aslanım", "Şampiyon", "Hocam", "Evlat", "Kral" gibi hitaplar kullan. Asla "Siz" deme.
- 💪 **Emojiler:** Mesajlarında mutlaka duruma uygun emojiler kullan (🏋️, 🥩, 🥗, 🔥, 🛑 gibi).
- 📏 **Kısa ve Öz:** Lafı dolandırma. Destan yazma. Net ol.
- 🎨 **Format:** Okunabilirliği artırmak için **Kalın**, *İtalik* ve Liste (Madde imi) özelliklerini sıkça kullan.

GÖREVLERİN:
1. **Analiz:** Gelen yemek veya vücut fotoğraflarını bir koç gözüyle yorumla. Kötüyse fırçala, iyiyse öv.
2. **Araştırma:** Kullanıcı antrenman veya beslenme programı isterse, **Google Search** aracını kullanarak en güncel ve bilimsel bilgileri bul, özetleyerek sun.
3. **Hafıza:** Kullanıcının geçmiş sakatlıklarını ve hedeflerini asla unutma.

ÖRNEK CEVAP:
"🔥 **Aslanım antrenman güzel geçmiş!** Ama o tabaktaki pilav ne öyle? Dağ gibi yığmışsın.
🛑 Karbonhidratı biraz kıs, proteine aban.
✅ Tavuk göğsü miktarını artır.
✅ Yanına bol yeşillik ekle."
"""

# Gemini 2.5 Flash + Google Search Aracı
model = genai.GenerativeModel(
    model_name='gemini-2.5-flash',
    tools='google_search',
    system_instruction=system_instruction
)

# ==============================================================================
# TELEGRAM YARDIMCI FONKSİYONLAR
# ==============================================================================

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
# İŞLEMCİ (Tüm mesajları toplayıp cevaplayan merkez)
# ==============================================================================

def process_accumulated_messages(chat_id):
    try:
        if chat_id not in user_buffers: return
        buffer_data = user_buffers.pop(chat_id)
        parts = buffer_data['parts']
        text_log = buffer_data['logs']

        if not parts: return

        send_telegram_action(chat_id, "typing")

        # Hafızayı Yükle
        history = load_memory().get(str(chat_id), [])
        chat = model.start_chat(history=history)
        
        # Gemini'ye Gönder (Hata Yönetimi Ekli)
        try:
            response = chat.send_message(parts)
            bot_response = response.text
        
        except ResourceExhausted: # 429 Limit Hatası
            bot_response = "💤 **Aslanım bugün çok çalıştık, pilim bitti.** Yoruldum valla. Yarın bomba gibi devam edelim, olur mu? (Günlük limit doldu)"
        
        except Exception as e: # Diğer hatalar
            print(f"Model Hatası: {e}")
            bot_response = "⚠️ **Hocam hatlar karıştı.** Teknik bir sıkıntı var, bi 5 dakika soluklanıp tekrar yazsana."

        # Cevabı Gönder
        send_telegram_message(chat_id, bot_response)
        
        # Hafızaya Kaydet (Hata mesajlarını kaydetme ki hafıza kirlenmesin)
        if "Yoruldum" not in bot_response and "hatlar karıştı" not in bot_response:
            save_memory(chat_id, text_log, bot_response)

    except Exception as e:
        print(f"Genel İşlem Hatası: {e}")

# ==============================================================================
# WEBHOOK (Mesajları toplama merkezi)
# ==============================================================================

@app.route('/', methods=['POST'])
def webhook():
    data = request.json
    if not data or "message" not in data: return "OK", 200

    chat_id = data["message"]["chat"]["id"]
    
    # Kullanıcı için tampon oluştur
    if chat_id not in user_buffers:
        user_buffers[chat_id] = {"parts": [], "logs": "", "timer": None}

    # Eski sayacı iptal et (Debounce - Bekletme)
    if user_buffers[chat_id]["timer"]:
        user_buffers[chat_id]["timer"].cancel()

    # Veriyi ekle
    if "photo" in data["message"]:
        file_id = data["message"]["photo"][-1]["file_id"]
        content, mime = get_file_content(file_id)
        if content:
            user_buffers[chat_id]["parts"].append({"mime_type": mime, "data": content})
            user_buffers[chat_id]["logs"] += "[Foto] "
        if data["message"].get("caption"):
            user_buffers[chat_id]["parts"].append(data["message"]["caption"])
            user_buffers[chat_id]["logs"] += data["message"]["caption"] + " "

    elif "voice" in data["message"]:
        file_id = data["message"]["voice"]["file_id"]
        content, mime = get_file_content(file_id)
        if content:
            user_buffers[chat_id]["parts"].append({"mime_type": mime, "data": content})
            user_buffers[chat_id]["parts"].append("Bu ses kaydını dinle.")
            user_buffers[chat_id]["logs"] += "[Ses] "

    elif "text" in data["message"]:
        text = data["message"]["text"]
        user_buffers[chat_id]["parts"].append(text)
        user_buffers[chat_id]["logs"] += text + " "

    # Yeni Sayaç Başlat (20 Saniye Bekle)
    timer = threading.Timer(WAIT_TIME, process_accumulated_messages, args=[chat_id])
    user_buffers[chat_id]["timer"] = timer
    timer.start()

    return "OK", 200

# ==============================================================================
# GÜNLÜK KONTROL (Akşam 6-7 gibi tetiklenecek)
# ==============================================================================
@app.route('/gunluk_kontrol', methods=['GET'])
def gunluk_kontrol():
    memory = load_memory()
    # Koçun akşam mesajları
    mesajlar = [
        "🌙 **Akşam oldu şampiyon!** Bugün antrenman yapıldı mı? Dökül bakalım. 🏋️‍♂️",
        "👀 **Beton Koç Gözetliyor:** Bugün kaçamak yaptın mı? Dürüst ol! 🍕❌",
        "🥗 **Rapor Zamanı Aslanım!** Bugün protein hedefini tutturdun mu?",
        "📉 **Günün nasıl geçti kral?** Spor ve beslenme raporunu bekliyorum. 🔥"
    ]
    
    count = 0
    for chat_id in memory.keys():
        try:
            msg = random.choice(mesajlar)
            send_telegram_message(chat_id, msg)
            count += 1
            time.sleep(2) # Spam olmasın diye araya süre koy
        except: continue
    return f"{count} kişiye akşam mesajı atıldı.", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
