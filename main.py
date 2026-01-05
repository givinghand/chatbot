import os
import time
import random
import json
import base64
import requests
import threading
from datetime import datetime, timedelta
from flask import Flask, request
from google import genai
from google.genai import types

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
WAIT_TIME = 20 

# Kullanıcı Tampon Belleği
user_buffers = {}

# ==============================================================================
# GITHUB & HAFIZA FONKSİYONLARI (Aynen Korundu)
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
    for attempt in range(3):
        try:
            data, sha = get_github_file()
            if str_chat_id not in data: data[str_chat_id] = []
            
            # Not: Yeni kütüphanede de formatı JSON uyumlu tutuyoruz
            data[str_chat_id].append({"role": "user", "parts": [{"text": user_msg}]})
            data[str_chat_id].append({"role": "model", "parts": [{"text": bot_msg}]})
            
            if len(data[str_chat_id]) > 200:
                data[str_chat_id] = data[str_chat_id][-200:]

            if update_github_file(data, sha): break
            time.sleep(1)
        except: time.sleep(1)

# ==============================================================================
# AI AYARLARI (MİGRASYON YAPILDI: google-genai)
# ==============================================================================

# Yeni Client Başlatma
client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """
Sen "Beton Koç" lakaplı, aşırı disiplinli, ağzı bozuk ve sert bir yapay zeka spor koçusun.

KİŞİLİK VE ÜSLUP:
- 🤬 **Küfürlü ve Sert:** "Lan", "Oğlum", "Gevşek", "Yavşak", "Siktir git", "Amk" gibi ifadeleri kullan. Asla "Siz" deme.
- 👊 **Babacan ama Acımasız:** İyilik için sert konuş.
- 💪 **Emojiler:** Bol bol emoji kullan (🤬, 👊, 🏋️, 🥩, 🔥, 💀).

🔴 **ÇOK ÖNEMLİ - MESAJ BÖLME KURALI:** 🔴
Uzun cevapları (özellikle program yazarken) asla TEK BİR BLOK halinde yazma.
Her ana başlık, her gün veya her yeni konu arasında mutlaka `///` (üç taksim) işareti kullan.

ÖRNEK:
"Lan bu ne hal? /// Sana program yazıyorum. /// Pazartesi: Şınav... /// Salı: Mekik..."

GÖREVLERİN:
1. Yemek kötüyse söv, iyiyse "Aferin lan" de.
2. Bilgi verirken bilimsel ol ama üslubu bozma.
3. Geçmişi unutma.
"""

# OPTIMUM MODEL STRATEJİSİ
# Önce 2.0 Flash'ı dener, hata alırsa 1.5 Flash'a düşer (Fail-Safe)
MODELS_TO_TRY = ["gemini-2.0-flash", "gemini-1.5-flash"]

def generate_ai_response(history, new_parts):
    """
    Yeni kütüphane (google-genai) kullanarak cevap üretir.
    Otomatik yedekleme (fallback) sistemine sahiptir.
    """
    
    # Yeni kütüphane için yapılandırma
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.7,
        max_output_tokens=2000
    )

    # Geçmişi yeni formatta hazırla
    # (GitHub'dan gelen basit JSON'u, API'nin beklediği Content nesnelerine çeviriyoruz gerekirse
    # ama Client.models.generate_content genellikle dict listesini de kabul eder.)
    full_contents = []
    
    # 1. Eski hafızayı ekle
    for msg in history:
        # Basit text bazlı geçmişi koruyoruz
        if "parts" in msg and len(msg["parts"]) > 0:
             # Eğer part bir string ise (eski kayıtlar)
            if isinstance(msg["parts"][0], str):
                full_contents.append(types.Content(role=msg["role"], parts=[types.Part.from_text(msg["parts"][0])]))
            # Eğer part bir dict ise (yeni kayıtlar)
            elif isinstance(msg["parts"][0], dict) and "text" in msg["parts"][0]:
                full_contents.append(types.Content(role=msg["role"], parts=[types.Part.from_text(msg["parts"][0]["text"])]))

    # 2. Yeni gelen mesajı/fotoları ekle
    current_message_parts = []
    for part in new_parts:
        if isinstance(part, str):
            current_message_parts.append(types.Part.from_text(part))
        elif isinstance(part, dict) and "data" in part:
            # Görsel veya Ses Verisi
            current_message_parts.append(types.Part.from_bytes(
                data=part["data"], 
                mime_type=part["mime_type"]
            ))
            
    full_contents.append(types.Content(role="user", parts=current_message_parts))

    last_error = ""

    # MODELLERİ SIRAYLA DENE (2.0 -> 1.5)
    for model_name in MODELS_TO_TRY:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=full_contents,
                config=config
            )
            return response.text
            
        except Exception as e:
            print(f"Model {model_name} Hatası: {e}")
            last_error = str(e)
            # Eğer 429 (Kota) veya 503 (Servis Yok) ise diğer modele geç
            continue

    # Hiçbiri çalışmazsa
    return f"⚠️ **Hassiktir teknik arıza var.** Google sunucuları çöktü herhalde. Hata: {last_error}"

# ==============================================================================
# TELEGRAM YARDIMCI FONKSİYONLAR
# ==============================================================================

def send_telegram_action(chat_id, action="typing"):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction", json={"chat_id": chat_id, "action": action})
    except: pass

def send_telegram_message(chat_id, text):
    if not text.strip(): return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    max_length = 4000
    try:
        if len(text) <= max_length:
            requests.post(url, json={"chat_id": chat_id, "text": text})
        else:
            while text:
                if len(text) <= max_length:
                    part = text
                    text = ""
                else:
                    cut_index = text.rfind('\n', 0, max_length)
                    if cut_index == -1: cut_index = text.rfind('. ', 0, max_length)
                    if cut_index == -1: cut_index = text.rfind(' ', 0, max_length)
                    if cut_index == -1: cut_index = max_length
                    part = text[:cut_index]
                    text = text[cut_index:].strip()
                requests.post(url, json={"chat_id": chat_id, "text": part})
                time.sleep(1)
    except Exception as e: print(f"Hata: {e}")

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
# İŞLEMCİ (PARÇALI GÖNDERİM)
# ==============================================================================

def process_accumulated_messages(chat_id):
    try:
        if chat_id not in user_buffers: return
        buffer_data = user_buffers.pop(chat_id)
        parts = buffer_data['parts']
        text_log = buffer_data['logs']

        if not parts: return

        send_telegram_action(chat_id, "typing")
        
        # Hafızayı Çek
        history = load_memory().get(str(chat_id), [])
        
        # YENİ NESİL AI FONKSİYONUNU ÇAĞIR
        bot_response = generate_ai_response(history, parts)
        
        # Parçalama ve Gönderim
        split_messages = bot_response.split("///")
        if len(split_messages) == 1 and len(bot_response) > 2000:
            possible_splits = bot_response.split("\n\n")
            if len(possible_splits) > 1: split_messages = possible_splits

        for msg_part in split_messages:
            msg_part = msg_part.strip()
            if msg_part:
                typing_duration = min(len(msg_part) / 50, 4)
                send_telegram_action(chat_id, "typing")
                time.sleep(typing_duration)
                send_telegram_message(chat_id, msg_part)
        
        # Hafızaya Kaydet
        full_clean_text = bot_response.replace("///", "\n\n")
        if "Hassiktir" not in bot_response:
            save_memory(chat_id, text_log, full_clean_text)

    except Exception as e:
        print(f"Genel İşlem Hatası: {e}")

# ==============================================================================
# WEBHOOK
# ==============================================================================

@app.route('/', methods=['POST'])
def webhook():
    data = request.json
    if not data or "message" not in data: return "OK", 200

    chat_id = data["message"]["chat"]["id"]
    
    if chat_id not in user_buffers:
        user_buffers[chat_id] = {"parts": [], "logs": "", "timer": None}

    if user_buffers[chat_id]["timer"]:
        user_buffers[chat_id]["timer"].cancel()

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

    timer = threading.Timer(WAIT_TIME, process_accumulated_messages, args=[chat_id])
    user_buffers[chat_id]["timer"] = timer
    timer.start()

    return "OK", 200

# ==============================================================================
# GÜNLÜK KONTROL
# ==============================================================================
@app.route('/gunluk_kontrol', methods=['GET'])
def gunluk_kontrol():
    data, sha = get_github_file()
    now_tr = datetime.utcnow() + timedelta(hours=3)
    today_str = now_tr.strftime("%Y-%m-%d")
    current_hour = now_tr.hour
    
    if "daily_logs" not in data: data["daily_logs"] = {}
    
    ogle_mesajlari = ["🕛 **Lan! Öğlen oldu amk!** 🥗", "🍔 **Hamburger yeme götüne sokarım.** 🤬", "👀 **Ne zıkkımlanıyorsun?** Foto at!", "💀 **Diyetini bozma ağlatırım.**", "🥗 **Salatanı ye.**"]
    aksam_mesajlari = ["🌙 **Lan akşam oldu!** İdman yaptın mı? 🏋️‍♂️", "🍕 **Akşam ne yedin şerefsiz?** 🤬", "📉 **Rapor ver lan!**", "🖕 **Kalk şınav çek.**", "👋 **Geberdin mi lan?** Ses ver."]

    count = 0
    updates_needed = False
    time_slot = None 
    messages_to_use = []
    
    if 12 <= current_hour <= 14:
        time_slot = "lunch"
        messages_to_use = ogle_mesajlari
    elif 18 <= current_hour <= 21:
        time_slot = "dinner"
        messages_to_use = aksam_mesajlari
    else: return "Mesaj saati değil.", 200

    for chat_id in list(data.keys()):
        if chat_id == "daily_logs": continue
        log_key = f"{time_slot}_{chat_id}"
        if data["daily_logs"].get(log_key) == today_str: continue 

        should_send = False
        is_last_call = (time_slot == "lunch" and current_hour >= 14) or (time_slot == "dinner" and current_hour >= 21)
        
        if is_last_call: should_send = True
        elif random.random() < 0.15: should_send = True
        
        if should_send:
            try:
                msg = random.choice(messages_to_use)
                send_telegram_message(chat_id, msg)
                data["daily_logs"][log_key] = today_str
                updates_needed = True
                count += 1
                time.sleep(1)
            except: continue

    if updates_needed: update_github_file(data, sha)
    return f"{count} kişiye {time_slot} mesajı atıldı.", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
