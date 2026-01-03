import os
import time
import random
import json
import base64
import requests
from flask import Flask, request
import google.generativeai as genai

app = Flask(__name__)

# ==============================================================================
# AYARLAR (Render Environment Variables Kısmına Girilecekler)
# ==============================================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# GitHub Ayarları (Hafızayı buraya kaydedeceğiz)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")       # Örn: ghp_xxxxxxxxxxxx
GITHUB_REPO = os.environ.get("GITHUB_REPO")         # Örn: aliyazici/beton-koc
GITHUB_FILE_PATH = "koc_hafizasi.json"              # Dosya adı

# ==============================================================================
# GITHUB HAFIZA YÖNETİMİ
# ==============================================================================

def get_github_file():
    """GitHub'dan hafıza dosyasını ve SHA kodunu çeker"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            content_b64 = response.json()['content']
            content_json = base64.b64decode(content_b64).decode('utf-8')
            sha = response.json()['sha']
            return json.loads(content_json), sha
        else:
            # Dosya henüz yoksa boş döndür
            return {}, None
    except Exception as e:
        print(f"GitHub Okuma Hatası: {e}")
        return {}, None

def update_github_file(data, sha):
    """GitHub'daki hafıza dosyasını günceller"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    # JSON verisini Base64 formatına çevir
    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    content_b64 = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')

    payload = {
        "message": "Beton Koç Hafıza Güncellemesi (Otomatik)",
        "content": content_b64
    }
    
    # Eğer dosya varsa SHA kodu ekle (Üzerine yazmak için şart)
    if sha:
        payload["sha"] = sha

    try:
        requests.put(url, headers=headers, json=payload)
    except Exception as e:
        print(f"GitHub Yazma Hatası: {e}")

# ==============================================================================
# KOÇUN HAFIZA FONKSİYONLARI
# ==============================================================================

def load_memory():
    data, _ = get_github_file()
    return data

def save_memory(chat_id, user_msg, bot_msg):
    # 1. Mevcut veriyi ve SHA'yı çek
    data, sha = get_github_file()
    str_chat_id = str(chat_id)
    
    if str_chat_id not in data:
        data[str_chat_id] = []
    
    # 2. Yeni konuşmayı ekle
    data[str_chat_id].append({"role": "user", "parts": [user_msg]})
    data[str_chat_id].append({"role": "model", "parts": [bot_msg]})
    
    # 3. Hafıza temizliği (Çok aşırı şişerse diye son 200 mesajı tutuyoruz)
    # Gemini 2.5 Flash'in hafızası çok geniştir ama GitHub dosya boyutu limiti için önlem.
    if len(data[str_chat_id]) > 200:
        data[str_chat_id] = data[str_chat_id][-200:]

    # 4. GitHub'a geri yükle
    update_github_file(data, sha)

# ==============================================================================
# YAPAY ZEKA AYARLARI (Beton Koç)
# ==============================================================================
genai.configure(api_key=GEMINI_API_KEY)

system_instruction = """
Sen profesyonel bir yapay zeka spor ve beslenme koçusun.
Adın: 'Beton Koç'.

KİŞİLİK ÖZELLİKLERİN:
1. Disiplinli, otoriter ama içten içe babacan (Askeri disiplin + Abi şefkati).
2. Asla resmi konuşma. "Siz" deme. Hitapların: "Brom", "Hocam", "Kral", "Kankam", "Bacıko".
3. Kullanıcı kaytarırsa sert çıkış, iyi iş yaparsa öv.

GÖREVLERİN:
- Yemek Fotoğrafı Gelirse: Kaloriyi tahmin et, makroları (protein/karb/yağ) yorumla. Sağlıklıysa "Afiyet olsun aslanım", kötüyse "Bu ne oğlum? Çöpe at onu" de.
- Vücut/Antrenman Fotoğrafı Gelirse: Formu yorumla, eksik bölgeleri söyle.
- Ses Kaydı Gelirse: Dikkatlice dinle ve koç tavrıyla cevap ver.
- HAFIZA: Kullanıcının geçmişini, sakatlıklarını, hedeflerini ASLA UNUTMA.

ÜSLUP:
Net, kısa ve motive edici. Destan yazma.
"""

# Model Seçimi: Gemini 2.5 Flash (Hızlı ve Geniş Hafızalı)
model = genai.GenerativeModel(
    model_name='gemini-2.5-flash', 
    system_instruction=system_instruction
)

# ==============================================================================
# TELEGRAM YARDIMCI FONKSİYONLAR
# ==============================================================================

def send_telegram_action(chat_id, action="typing"):
    """Yazıyor... veya Ses kaydediyor... efekti"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction"
        requests.post(url, json={"chat_id": chat_id, "action": action})
    except: pass

def send_telegram_message(chat_id, text):
    """Kullanıcıya mesaj gönderir"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Mesaj Gönderme Hatası: {e}")

def get_file_content(file_id):
    """Telegram sunucusundan dosya (foto/ses) indirir"""
    try:
        # Dosya yolunu bul
        path_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}"
        path_resp = requests.get(path_url).json()
        
        if not path_resp.get('ok'): return None, None

        file_path = path_resp['result']['file_path']
        
        # Dosyayı indir
        download_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
        file_content = requests.get(download_url).content
        
        # MIME type belirle
        mime_type = "image/jpeg" if "photos" in file_path else "audio/ogg"
        return file_content, mime_type
    except: return None, None

# ==============================================================================
# WEBHOOK (ANA GİRİŞ KAPISI)
# ==============================================================================

@app.route('/', methods=['POST'])
def webhook():
    data = request.json
    
    # Boş istek kontrolü
    if not data or "message" not in data:
        return "OK", 200

    chat_id = data["message"]["chat"]["id"]
    
    try:
        # 1. İnsansı Bekleme Efekti
        send_telegram_action(chat_id, "typing")
        # 2 ile 5 saniye arası rastgele bekle
        time.sleep(random.randint(2, 5))

        # 2. Gelen Mesajı İşle
        user_parts = []
        user_text_log = "" 

        # A) Fotoğraf
        if "photo" in data["message"]:
            file_id = data["message"]["photo"][-1]["file_id"]
            content, mime = get_file_content(file_id)
            if content:
                user_parts.append({"mime_type": mime, "data": content})
                user_text_log += "[Kullanıcı Fotoğraf Attı] "
            
            caption = data["message"].get("caption", "Hocam şu fotoğrafa bir bak, yorumla.")
            user_parts.append(caption)
            user_text_log += caption

        # B) Ses Kaydı
        elif "voice" in data["message"]:
            file_id = data["message"]["voice"]["file_id"]
            content, mime = get_file_content(file_id)
            if content:
                user_parts.append({"mime_type": mime, "data": content})
                user_parts.append("Bu ses kaydını dinle ve cevapla.")
                user_text_log += "[Ses Kaydı] "

        # C) Metin
        elif "text" in data["message"]:
            user_parts.append(data["message"]["text"])
            user_text_log += data["message"]["text"]

        else:
            return "OK", 200 

        # 3. GitHub'dan Geçmişi Yükle ve Cevap Üret
        history = load_memory().get(str(chat_id), [])
        
        chat = model.start_chat(history=history)
        response = chat.send_message(user_parts)
        bot_response = response.text

        # 4. Cevabı Gönder ve Kaydet
        send_telegram_message(chat_id, bot_response)
        save_memory(chat_id, user_text_log, bot_response)

    except Exception as e:
        print(f"Hata: {e}")
        send_telegram_message(chat_id, "Hafıza yüklenirken bir sorun oldu ama ben buradayım aslanım. Tekrar yaz.")

    return "OK", 200

# ==============================================================================
# GÜNLÜK KONTROL (Cron Job Tetikleyicisi)
# ==============================================================================
@app.route('/gunluk_kontrol', methods=['GET'])
def gunluk_kontrol():
    # Hafızadaki kullanıcı listesini çek
    memory = load_memory()
    user_ids = memory.keys()
    
    mesajlar = [
        "Akşam raporu ver aslanım! Bugün ne yedin?",
        "İdman yapıldı mı? Dürüst ol.",
        "Hedefe odaklan şampiyon. Bugün kaçamak var mı?",
        "Beton Koç dinliyor. Günün nasıl geçti?",
        "Bugün protein hedefini tutturdun mu? Rapor ver."
    ]
    
    count = 0
    for chat_id in user_ids:
        try:
            msg = random.choice(mesajlar)
            send_telegram_message(chat_id, msg)
            count += 1
            time.sleep(2) # Spam olmasın diye aralıklı at
        except:
            continue
            
    return f"{count} kişiye mesaj atıldı.", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
