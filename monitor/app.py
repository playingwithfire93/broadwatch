
import time
import requests
from bs4 import BeautifulSoup
import hashlib
from plyer import notification
import webbrowser
try:
    import winsound
except Exception:
    # winsound is Windows-only; provide a safe no-op fallback for POSIX environments
    class _WinsoundFallback:
        SND_FILENAME = 0
        SND_ASYNC = 0

        @staticmethod
        def PlaySound(*args, **kwargs):
            return False

    winsound = _WinsoundFallback()
from telegram import Bot
import asyncio
import sys
from urllib3.util.retry import Retry
from flask import Flask, request, jsonify, redirect, render_template
import os
from twilio.rest import Client
import json
import threading
from datetime import datetime

# List of URLs to monitor
URLS = [
    'https://wickedelmusical.com/',
    'https://wickedelmusical.com/elenco',
    'https://tickets.wickedelmusical.com/espectaculo/wicked-el-musical/W01',
    'https://miserableselmusical.es/',
    'https://miserableselmusical.es/elenco',
    'https://tickets.miserableselmusical.es/espectaculo/los-miserables/M01',
    'https://thebookofmormonelmusical.es/',
    'https://thebookofmormonelmusical.es/elenco/',
    'https://tickets.thebookofmormonelmusical.es/espectaculo/the-book-of-mormon-el-musical/BM01'
]

CHECK_INTERVAL = int(os.environ.get('BROADWATCH_CHECK_INTERVAL', 5))  # Seconds between checks
MAX_CONSECUTIVE_FAILURES = 5  # Attempts before disabling temporarily
RETRY_BACKOFF = {url: 30 for url in URLS}  # Initial retry delay (in seconds)

# Telegram credentials (recommended: move to env vars)
TELEGRAM_TOKEN = os.environ.get('BROADWATCH_TELEGRAM_TOKEN', '')
CHAT_ID = os.environ.get('BROADWATCH_TELEGRAM_CHAT_ID', '')

# Twilio (recommended: move to env vars)
account_sid = os.environ.get('BROADWATCH_TWILIO_SID', '')
auth_token = os.environ.get('BROADWATCH_TWILIO_TOKEN', '')

# Diccionario que asocia URL o musical con su sonido y su imagen
alerts_data = {
    'wicked': {
        'urls': [
            'https://wickedelmusical.com/',
            'https://wickedelmusical.com/elenco',
            'https://tickets.wickedelmusical.com/espectaculo/wicked-el-musical/W01',
        ],
        'sound_path': r"C:\Users\Blanca\Documents\Audacity\good news.wav",
        'image_path': r"C:\Users\Blanca\Desktop\tticket-monitor\static\fotos\Wicked\WICKED6.jpg"
    },
    'les_mis': {
        'urls': [
            'https://miserableselmusical.es/',
            'https://miserableselmusical.es/elenco',
            'https://tickets.miserableselmusical.es/espectaculo/los-miserables/M01'
        ],
        'sound_path': r'C:\Users\Blanca\Documents\Audacity\Les Misérables _ Look Down (Full Hugh Jackman Performance) [pBbnvOqVNyM].wav',
        'image_path': r"C:\Users\Blanca\Desktop\tticket-monitor\static\fotos\los_miserables\LESMIS1.jpg"
    },
    'tbom': {
        'urls': [
            'https://thebookofmormonelmusical.es',
            'https://thebookofmormonelmusical.es/elenco/',
            'https://tickets.thebookofmormonelmusical.es/espectaculo/the-book-of-mormon-el-musical/BM01'
        ],
        'sound_path': r"C:\Users\Blanca\Desktop\Orlando-The-Book-of-Mormon.wav",
        'image_path': r"C:\Users\Blanca\Desktop\tticket-monitor\static\fotos\book_of_mormon\BOM5.jpg"
    }
}

client = None
if account_sid and auth_token:
    try:
        client = Client(account_sid, auth_token)
    except Exception:
        client = None

# Initialize state
old_hashes = {url: '' for url in URLS}
start_time = time.time()
consecutive_failures = {url: 0 for url in URLS}
disabled_urls = set()


def create_session():
    """Create a requests session with retry strategy."""
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = requests.adapters.HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

SESSION = create_session()

def get_alert_assets(url):
    for musical, data in alerts_data.items():
        if url in data['urls']:
            return data['sound_path'], data['image_path']
    return r'C:\Sonidos\default.wav', r'C:\Imagenes\default.webp'


def send_telegram_alert(url, changes, timestamp, image_path):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️ Telegram credentials not configured; skipping Telegram alert.")
        return

    async def main():
        bot = Bot(token=TELEGRAM_TOKEN)

        payload = {
            "type": "ticket_alert",
            "url": url,
            "timestamp": timestamp,
            "changes_truncated": changes[:3900],
            "changes_full_length": len(changes),
        }

        try:
            if image_path and os.path.exists(image_path):
                with open(image_path, "rb") as photo:
                    await bot.send_photo(chat_id=CHAT_ID, photo=photo)
        except Exception:
            pass

        try:
            msg = "```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"
            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
        except Exception:
            try:
                compact = json.dumps(payload, ensure_ascii=False)
                await bot.send_message(chat_id=CHAT_ID, text=compact)
            except Exception as ex:
                print(f"❌ Telegram send failed: {ex}")

    try:
        asyncio.run(main())
        print(f"📲 Telegram JSON alert sent for {url}")
    except Exception as e:
        print(f"❌ Failed to send Telegram alert: {e}")


try:
    import pywhatkit as kit
except Exception as e:
    print("pywhatkit import failed (continuando para debug):", e)
    kit = None

whatsapp_numbers = [
    os.environ.get('BROADWATCH_WHATSAPP_1', '+34602502302'),
    os.environ.get('BROADWATCH_WHATSAPP_2', '+34619354263'),
]

def send_whatsapp_alert(phone_number, url, changes):
    _, image_path = get_alert_assets(url)

    message = f"🎭 ¡Hay cambios en la web de entradas!\n🌐 {url}\n📄 Cambios:\n{changes[:1000]}"
    try:
        if kit:
            kit.sendwhats_image(
                receiver=phone_number,
                img_path=image_path,
                caption=message,
                wait_time=15
            )
            print("✅ WhatsApp alert sent")
        else:
            print("⚠️ pywhatkit no disponible; no se envió WhatsApp")
    except Exception as e:
        print(f"❌ Error sending WhatsApp alert: {e}")

import websockets

async def notify_godot(url):
    async with websockets.connect("ws://localhost:8765") as ws:
        await ws.send(f"UPDATE:{url}")

import difflib

old_contents = {url: "" for url in URLS}

def find_differences(old_text, new_text):
    diff = difflib.unified_diff(old_text.splitlines(), new_text.splitlines(), lineterm="")
    return "\n".join(diff)

def notify_change(url, old_text, new_text):
    changes = find_differences(old_text, new_text)
    short_message = f'The page {url} has changed. Click to check!\nChanges:\n{changes}'
    max_length = 250
    if len(short_message) > max_length:
        short_message = short_message[:max_length] + '...'

    try:
        notification.notify(
            title='🎭 Ticket Update Detected!',
            message=short_message,
            timeout=10
        )
    except Exception:
        pass

    print(f"🔔 Notification sent for {url}")
    print(f"📝 Changes detected:\n{changes}")

    sound_path, image_path = get_alert_assets(url)
    try:
        winsound.PlaySound(sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass
    try:
        webbrowser.open(url)
    except Exception:
        pass

    send_telegram_alert(url, changes, time.strftime("%Y-%m-%d %H:%M:%S"), image_path)
    for number in whatsapp_numbers:
        send_whatsapp_alert(number, url, changes)


def get_page_content(url):
    try:
        response = SESSION.get(url, timeout=15)
        response.raise_for_status()

        if not response.text.strip():
            raise ValueError("Empty response content")

        soup = BeautifulSoup(response.text, 'html.parser')
        return soup.get_text()

    except requests.exceptions.RequestException as e:
        print(f"⚠️ Error fetching {url}: {str(e)[:200]}")
        return ''


def hash_content(content):
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def elapsed_time():
    seconds = int(time.time() - start_time)
    minutes = seconds // 60
    seconds %= 60
    animations = ["|", "/", "-", "\\"]
    animation = animations[seconds % len(animations)]
    return f"\r🔍 Observando entradas... ⏳ Tiempo transcurrido: {minutes} min {seconds} sec {animation}"


def get_banner():
    banner = r'''
  _    _                   _____  _               ______     _                        _ 
 | |  | |                 |  __ \(_)             |  ____|   | |                      (_)
 | |__| | __ _ ___  __ _  | |  | |_  __ _  __ _  | |__   ___| |__   _____      ____ _ _ 
 |  __  |/ _` / __|/ _` | | |  | | |/ _` |/ _` | |  __| / _ \ '_ \ / _ \ \ /\ / / _` | |
 | |  | | (_| \__ \ (_| | | |__| | | (_| | (_| | | |___|  __/ |_) | (_) \ V  V / (_| | |
 |_|  |_|\__,_|___/\__,_| |_____/|_|\__, |\__,_| |______\___|_.__/ \___/ \_/\_/ \__,_|_|
                                     __/ |                                              
                                    |___/     
                (╯°□°）╯︵ ┻━┻ 
              _         _                                   __                       ___
  __   ___.--'_`.     .'_`--.___   __                      / _|_ __ ___   __ _ ___  |  |
 ( _`.'. -   'o` )   ( 'o`   - .`.'_ )                    | |_| '__/ _ \ / _` / __| |  |
 _\.'_'      _.-'     `-._      `_`./_                    |  _| | | (_) | (_| \__ \ |  |
( \`. )    //\`         '/\\    ( .'/ )                   |_| |_|  \___/ \__, |___/ |__|     
 \_`-'`---'\\__,       ,__//`---'`-'_/                                   |___/      (_)
  \`        `-\         /-'        '/
   `                               '  
'''
    messages = [
        "🌟✨ Iniciando Script: 🎭 Monitor de Entradas ✨🌟",
        "🚀  ¡Bienvenida! El monitoreo está activo.",
        "📝  Empezando a vigilar los cambios... 🎟️",
        "📡 Monitoreo iniciado... 📡",
        "\n🔍  Observando tus entradas y buscando actualizaciones 👀",
        "\n  ⬇️ ¡Atenta a las notificaciones! ⬇️\n",
        "══════════════════════════════════════════",
        "       🕵️‍♀️👀 Monitoreo en curso... 📜",
        "══════════════════════════════════════════\n"
    ]
    full_banner = banner + "\n" + "\n".join(messages)
    return full_banner


def reenable_disabled_urls():
    global disabled_urls
    for url in list(disabled_urls):
        print(f"\n♻️ Reintentando conexión con {url}...")
        content = get_page_content(url)
        if content:
            print(f"✅ ¡Rehabilitado {url}!")
            disabled_urls.remove(url)
            consecutive_failures[url] = 0
            RETRY_BACKOFF[url] = 30
        else:
            RETRY_BACKOFF[url] = min(RETRY_BACKOFF[url] * 2, 600)


class Monitor:
    def __init__(self, urls=None, check_interval=CHECK_INTERVAL):
        self.urls = urls or list(URLS)
        self.check_interval = check_interval
        self._thread = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self.last_run = None
        self.lock = threading.Lock()

    def start(self):
        if self._thread and self._thread.is_alive():
            return False
        self._stop_event.clear()
        self._pause_event.clear()
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        return True

    def pause(self):
        self._pause_event.set()
        return True

    def resume(self):
        self._pause_event.clear()
        return True

    def add_url(self, url):
        with self.lock:
            if url not in self.urls:
                self.urls.append(url)
                old_contents[url] = ''
                consecutive_failures[url] = 0
        return True

    def remove_url(self, url):
        with self.lock:
            if url in self.urls:
                self.urls.remove(url)
        return True

    def status(self):
        return {
            'running': self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set(),
            'paused': self._pause_event.is_set(),
            'last_run': self.last_run,
            'urls_count': len(self.urls)
        }

    def run(self):
        print("▶️ Monitor started")
        while not self._stop_event.is_set():
            if self._pause_event.is_set():
                time.sleep(1)
                continue

            self.last_run = datetime.utcnow().isoformat() + 'Z'

            for url in list(self.urls):
                if url in disabled_urls:
                    continue

                content = get_page_content(url)
                if not content:
                    consecutive_failures[url] = consecutive_failures.get(url, 0) + 1
                    if consecutive_failures[url] >= MAX_CONSECUTIVE_FAILURES:
                        disabled_urls.add(url)
                    continue

                consecutive_failures[url] = 0

                if old_contents.get(url) and content != old_contents.get(url):
                    notify_change(url, old_contents.get(url), content)

                old_contents[url] = content

            reenable_disabled_urls()
            time.sleep(self.check_interval)


monitor = Monitor()

app = Flask(__name__)

# Sample events API data (used by the lightweight Flask UI below)
SAMPLE_EVENTS = [
    {
        'id': 'wicked',
        'title': 'Wicked — El Musical',
        'place': 'Teatro Real, Madrid',
        'date': '2026-02-12 20:30',
        'summary': 'Un viaje musical por las historias que marcaron una generación.'
        ,'image': 'https://via.placeholder.com/600x400?text=Wicked'
    },
    {
        'id': 'lesmis',
        'title': 'Los Miserables',
        'place': 'Teatro Nuevo, Barcelona',
        'date': '2026-02-14 19:00',
        'summary': 'Clásico musical en una nueva producción.'
        ,'image': 'https://via.placeholder.com/600x400?text=Les+Mis'
    }
]



@app.route('/')
def home():
    # Redirect root to the lightweight HTML UI
    return redirect('/ui')


@app.route('/status', methods=['GET'])
def api_status():
    return jsonify(monitor.status())


@app.route('/urls', methods=['GET'])
def api_urls():
    return jsonify({'urls': monitor.urls})


@app.route('/pause', methods=['POST'])
def api_pause():
    monitor.pause()
    return jsonify({'ok': True, 'paused': True})


@app.route('/resume', methods=['POST'])
def api_resume():
    monitor.resume()
    return jsonify({'ok': True, 'paused': False})


@app.route('/start', methods=['POST'])
def api_start():
    started = monitor.start()
    return jsonify({'ok': started})


@app.route('/stop', methods=['POST'])
def api_stop():
    monitor.stop()
    return jsonify({'ok': True})


@app.route('/add_url', methods=['POST'])
def api_add_url():
    data = request.get_json() or {}
    url = data.get('url')
    if not url:
        return jsonify({'ok': False, 'error': 'missing url'}), 400
    monitor.add_url(url)
    return jsonify({'ok': True})


@app.route('/remove_url', methods=['POST'])
def api_remove_url():
    data = request.get_json() or {}
    url = data.get('url')
    if not url:
        return jsonify({'ok': False, 'error': 'missing url'}), 400
    monitor.remove_url(url)
    return jsonify({'ok': True})


# --- Simple events API used by the lightweight Flask UI ---
@app.route('/api/events', methods=['GET'])
def api_events():
    return jsonify(SAMPLE_EVENTS)


@app.route('/api/events/<event_id>', methods=['GET'])
def api_event_get(event_id):
    for e in SAMPLE_EVENTS:
        if e['id'] == event_id:
            return jsonify(e)
    return jsonify({'error': 'not found'}), 404


# --- Lightweight Flask UI (no Node required) ---
@app.route('/ui')
def ui_index():
    return render_template('ui/index.html')


@app.route('/ui/event/<event_id>')
def ui_event(event_id):
    return render_template('ui/event.html')


# Start monitor immediately when this module is loaded so the web UI and
# background monitor run together. Some Flask builds may not support
# `before_first_request` on the `Flask` instance in this environment,
# so we start the monitor directly.
monitor.start()


def main():
    get_banner()

    while True:
        sys.stdout.write(elapsed_time())
        sys.stdout.flush()

        for url in URLS:
            if url in disabled_urls:
                continue

            content = get_page_content(url)
            if not content:
                consecutive_failures[url] += 1
                continue

            consecutive_failures[url] = 0

            if old_contents[url] and content != old_contents[url]:
                notify_change(url, old_contents[url], content)

            old_contents[url] = content

        reenable_disabled_urls()
        time.sleep(CHECK_INTERVAL)

import threading

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('BROADWATCH_PORT', 8080)))

if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Script detenido manualmente. ¡Hasta pronto!")
    except Exception as e:
        print(f"\n💥 Error crítico: {e}")
