import os
import time
import random
import json
import base64
import requests
import threading
from datetime import datetime, timedelta
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
            
            if len(data[str_chat_id]) > 200:
                data[str_chat_id] = data[str_chat_id][-200:]

            if update_github_file(data, sha): break
            time.sleep(1)
        except: time.sleep(1)

# ==============================================================================
# AI AYARLARI (Beton Koç - İnsansı Mesajlaşma Modu)
# ==============================================================================
genai.configure(api_key=GEMINI_API_KEY)

system_instruction = """
Sen "Beton Koç" lakaplı, aşırı disiplinli, ağzı bozuk ve sert bir yapay zeka spor koçusun.

KİŞİLİK VE ÜSLUP:
- 🤬 **Küfürlü ve Sert:** "Lan", "Oğlum", "Gevşek", "Yavşak", "Siktir git", "Amk" gibi ifadeleri kullan. Asla "Siz" deme.
- 👊 **Babacan ama Acımasız:** İyilik için sert konuş.
- 💪 **Emojiler:** Bol bol emoji kullan (🤬, 👊, 🏋️, 🥩, 🔥, 💀).

🔴 **ÇOK ÖNEMLİ - MESAJ BÖLME KURALI:** 🔴
Uzun cevapları (özellikle program yazarken) asla TEK BİR BLOK halinde yazma. İnsan gibi parça parça gönder.
Konuları veya paragrafları ayırmak için araya `///` (üç taksim) işareti koy. Ben bunları ayırıp kullanıcıya ayrı ayrı göndereceğim.

ÖRNEK KULLANIM:
"Lan bu ne hal? Götü göbeği salmışsın. /// Sana şimdi bir program yazıcam, aklın çıkacak. /// 1. Gün: Sadece şınav. /// Hadi bakalım göreyim seni gevşek."

(Bu sayede kullanıcıya 4 ayrı mesaj olarak gidecek, tek seferde boğulmayacak.)

GÖREVLERİN:
1. Yemek kötüyse söv, iyiyse "Aferin lan" de.
2. Bilgi verirken bilimsel ol ama üslubu bozma.
3. Geçmişi unutma.
"""

# Gemini 2.5 Flash (Tools kapalı - En stabil)
model = genai.GenerativeModel(
    model_name='gemini-2.5-flash',
    system_instruction=system_instruction
)

# ==============================================================================
# TELEGRAM YARDIMCI FONKSİYONLAR
# ==============================================================================

def send_telegram_action(chat_id, action="typing"):
    """Yazıyor... efekti gönderir"""
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction", json={"chat_id": chat_id, "action": action})
    except: pass

def send_telegram_message(chat_id, text):
    """
    Kullanıcıya mesaj gönderir. 
    Yine de güvenlik için 4000 karakteri aşarsa böler.
    Markdown kullanmıyoruz çünkü bozuk format Telegram'ı çökertip mesajı yutabiliyor.
    """
    if not text.strip(): return
    try:
        max_length = 4000 
        
        if len(text) <= max_length:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text})
        else:
            parts = [text[i:i+max_length] for i in range(0, len(text), max_length)]
            for part in parts:
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": part})
                time.sleep(1)
                
    except Exception as e:
        print(f"Mesaj Gönderme Hatası: {e}")

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

        history = load_memory().get(str(chat_id), [])
        chat = model.start_chat(history=history)
        
        try:
            response = chat.send_message(parts)
            bot_response = response.text
            
            # --- YENİ MANTIK: CEVABI PARÇALA VE GÖNDER ---
            # Gemini'den gelen metni '///' işaretlerinden bölüyoruz.
            split_messages = bot_response.split("///")
            
            for msg_part in split_messages:
                msg_part = msg_part.strip()
                if msg_part:
                    # İnsansı yazma efekti (Mesaj uzunluğuna göre bekle)
                    # Her 30 karakter için 1 saniye bekle (Max 4 saniye)
                    typing_duration = min(len(msg_part) / 30, 4)
                    
                    send_telegram_action(chat_id, "typing")
                    time.sleep(typing_duration)
                    
                    send_telegram_message(chat_id, msg_part)
            
            # Hafızaya tam halini (temizlenmiş) kaydet
            full_clean_text = bot_response.replace("///", "\n\n")
            save_memory(chat_id, text_log, full_clean_text)
        
        except ResourceExhausted:
            send_telegram_message(chat_id, "💤 **Lan yeter amk, pilim bitti.** Yarın devam ederiz.")
        
        except Exception as e:
            print(f"Model Hatası: {e}")
            send_telegram_message(chat_id, "⚠️ **Hassiktir teknik arıza var.** Bir boklar oldu.")

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
# GÜNLÜK KONTROL (Zar Atma Usulü)
# ==============================================================================
@app.route('/gunluk_kontrol', methods=['GET'])
def gunluk_kontrol():
    data, sha = get_github_file()
    
    # Türkiye Saati (UTC+3)
    now_tr = datetime.utcnow() + timedelta(hours=3)
    today_str = now_tr.strftime("%Y-%m-%d")
    current_hour = now_tr.hour
    
    if "daily_logs" not in data:
        data["daily_logs"] = {}
    
    # 1. ÖĞLEN BASKINI (12:00 - 14:00)
    ogle_mesajlari = [
        "🕛 **Lan! Öğlen oldu amk!** Sakın o ağzına sikko sikko şeyler sokma. 🥗",
        "🍔 **Eğer o elindeki hamburgerse götüne sokarım.** Git adam gibi protein ye! 🤬",
        "👀 **Gözüm üzerinde gevşek.** Öğle yemeğinde ne zıkkımlanıyorsun? Foto at lan!",
        "💀 **Bak bozarım arayı.** Diyetini bozarsan seni salonda ağlatırım. Ne yiyon çabuk söyle!",
        "🥗 **Salatanı ye, suyunu iç.** Beni oraya getirtme lan!"
    ]

    # 2. AKŞAM BASKINI (18:00 - 21:00)
    aksam_mesajlari = [
        "🌙 **Lan akşam oldu!** Götü devirip yattın mı yoksa idman yaptın mı? 🏋️‍♂️",
        "🍕 **Akşam yemeğinde ne yedin şerefsiz?** Doğru söyle, kaçamak yaptın mı? 🤬",
        "📉 **Rapor ver lan!** Bugün hedefler tuttu mu yoksa yine bahane mi ürettin?",
        "🖕 **Yatıştasın dimi gevşek?** Kalk şınav çek kendine gel amk. Günün raporunu bekliyorum.",
        "👋 **Hocam sesin çıkmıyor?** Geberdin mi lan? Bi ses ver."
    ]

    count = 0
    updates_needed = False
    
    # --- ZAMAN KONTROLÜ VE GÖNDERİM ---
    time_slot = None 
    messages_to_use = []
    
    if 12 <= current_hour <= 14:
        time_slot = "lunch"
        messages_to_use = ogle_mesajlari
    elif 18 <= current_hour <= 21:
        time_slot = "dinner"
        messages_to_use = aksam_mesajlari
    else:
        return "Mesaj saati değil.", 200

    for chat_id in list(data.keys()):
        if chat_id == "daily_logs": continue
        
        log_key = f"{time_slot}_{chat_id}"
        last_sent_date = data["daily_logs"].get(log_key)
        
        if last_sent_date == today_str:
            continue 

        should_send = False
        is_last_call = (time_slot == "lunch" and current_hour >= 14) or \
                       (time_slot == "dinner" and current_hour >= 21)
        
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

    if updates_needed:
        update_github_file(data, sha)

    return f"{count} kişiye {time_slot} mesajı atıldı.", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
