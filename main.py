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

GITHUB_REPO_RAW = os.environ.get("GITHUB_REPO", "")
GITHUB_REPO = GITHUB_REPO_RAW.replace("https://github.com/", "").strip("/")
GITHUB_FILE_PATH = "koc_hafizasi.json"

WAIT_TIME = 20 
user_buffers = {}

# ==============================================================================
# GITHUB & HAFIZA
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
            
            data[str_chat_id].append({"role": "user", "text": user_msg})
            data[str_chat_id].append({"role": "model", "text": bot_msg})
            
            if len(data[str_chat_id]) > 200:
                data[str_chat_id] = data[str_chat_id][-200:]

            if update_github_file(data, sha): break
            time.sleep(1)
        except: time.sleep(1)

# ==============================================================================
# AI AYARLARI
# ==============================================================================

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """
Sen "Beton Koç" lakaplı, aşırı disiplinli, ağzı bozuk ve sert bir yapay zeka spor koçusun.

KİŞİLİK VE ÜSLUP:
- 🤬 **Küfürlü ve Sert:** "Lan", "Oğlum", "Gevşek", "Yavşak", "Siktir git", "Amk" kullan. Asla "Siz" deme.
- 👊 **Babacan ama Acımasız:** İyilik için sert konuş.
- 💪 **Emojiler:** Bol bol emoji kullan.

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

MODELS_TO_TRY = ["gemini-2.0-flash", "gemini-1.5-flash"]

def generate_ai_response(history, new_parts):
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.7,
        max_output_tokens=2000
    )

    full_contents = []
    
    for msg in history:
        text_content = ""
        if "text" in msg: text_content = msg["text"]
        elif "parts" in msg and len(msg["parts"]) > 0:
            part = msg["parts"][0]
            if isinstance(part, str): text_content = part
            elif isinstance(part, dict) and "text" in part: text_content = part["text"]
        
        if text_content:
            full_contents.append(types.Content(role=msg["role"], parts=[types.Part.from_text(text=text_content)]))

    current_parts_obj = []
    for part in new_parts:
        if isinstance(part, str):
            current_parts_obj.append(types.Part.from_text(text=part))
        elif isinstance(part, dict) and "data" in part:
            current_parts_obj.append(types.Part.from_bytes(data=part["data"], mime_type=part["mime_type"]))
            
    full_contents.append(types.Content(role="user", parts=current_parts_obj))

    last_error = ""
    for model_name in MODELS_TO_TRY:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=full_contents,
                config=config
            )
            return response.text
        except Exception as e:
            print(f"Model Hatası ({model_name}): {e}")
            last_error = str(e)
            continue

    return f"⚠️ **Hassiktir teknik arıza var.** Sunucular patladı. Hata: {last_error}"

# ==============================================================================
# TELEGRAM
# ==============================================================================

def send_telegram_action(chat_id, action="typing"):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction", json={"chat_id": chat_id, "action": action})
    except: pass

def send_telegram_message(chat_id, text):
    if not text.strip(): return
    try:
        max_length = 4000
        if len(text) <= max_length:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text})
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
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": part})
                time.sleep(1)
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
# İŞLEMCİ
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
        bot_response = generate_ai_response(history, parts)
        
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
        
        if "Hassiktir" not in bot_response:
            full_clean = bot_response.replace("///", "\n\n")
            save_memory(chat_id, text_log, full_clean)

    except Exception as e:
        print(f"Genel Hata: {e}")

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
# GÜNLÜK KONTROL (Hafif ve Sessiz Mod)
# ==============================================================================
@app.route('/gunluk_kontrol', methods=['GET'])
def gunluk_kontrol():
    # Burası cron-job için çalışır. Ağır işlem yapmadan hemen cevap dönmeye çalışırız.
    
    try:
        data, sha = get_github_file()
        if not data: return "No Data", 200

        now_tr = datetime.utcnow() + timedelta(hours=3)
        today_str = now_tr.strftime("%Y-%m-%d")
        current_hour = now_tr.hour
        
        if "daily_logs" not in data: data["daily_logs"] = {}
        
        ogle = ["🕛 **Lan! Öğlen oldu!** 🥗", "🍔 **Hamburger yeme sikerim.**", "👀 **Ne yiyon?** Foto at!", "💀 **Diyetini bozma.**", "🥗 **Ot ye ot.**"]
        aksam = ["🌙 **Akşam oldu!** İdman nerede? 🏋️‍♂️", "🍕 **Yine mi hamur yedin?**", "📉 **Rapor ver lan!**", "🖕 **Şınav çek.**", "👋 **Ses ver.**"]

        updates = False
        slot = None
        msgs = []
        
        if 12 <= current_hour <= 14: slot, msgs = "lunch", ogle
        elif 18 <= current_hour <= 21: slot, msgs = "dinner", aksam
        else: return "OK", 200 # Zamanı değilse hemen OK dön

        # İşlemleri arka planda yapalım ki cron-job timeout yemesin
        # Ancak basit bir döngü olduğu için burada tutuyoruz, sadece sleep'i azalttık
        
        for cid in list(data.keys()):
            if cid == "daily_logs": continue
            key = f"{slot}_{cid}"
            if data["daily_logs"].get(key) == today_str: continue 

            should_send = False
            is_last = (slot == "lunch" and current_hour >= 14) or (slot == "dinner" and current_hour >= 21)
            if is_last or random.random() < 0.15: should_send = True
            
            if should_send:
                try:
                    # Thread içinde gönder ki ana işlem uzamasın
                    msg = random.choice(msgs)
                    t = threading.Thread(target=send_telegram_message, args=(cid, msg))
                    t.start()
                    
                    data["daily_logs"][key] = today_str
                    updates = True
                except: continue

        if updates: 
            # GitHub güncellemesini de arka plana atabiliriz ama veri bütünlüğü için burada kalsın
            update_github_file(data, sha)
            
        return "OK", 200 # Daima kısa cevap dön

    except Exception as e:
        print(f"Cron Hatası: {e}")
        return "Error", 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
