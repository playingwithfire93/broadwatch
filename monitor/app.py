import time
import requests
from bs4 import BeautifulSoup
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
try:
    from plyer import notification
except Exception:
    notification = None
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
            # No-op on non-Windows platforms
            return None

    winsound = _WinsoundFallback()
from telegram import Bot
import asyncio
import sys
from urllib3.util.retry import Retry
from flask import Flask, request, jsonify, redirect, render_template, Response
import os
from twilio.rest import Client
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
import importlib

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


CHECK_INTERVAL = int(os.environ.get('BROADWATCH_CHECK_INTERVAL', 60))  # Seconds between checks (60s por defecto para no ser bloqueada)
MAX_CONSECUTIVE_FAILURES = 5  # Attempts before disabling temporarily
RETRY_BACKOFF = {url: 30 for url in URLS}  # Initial retry delay (in seconds)

# Telegram credentials (recommended: move to env vars)
# Prefer standard names if available (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID),
# fall back to BROADWATCH_* names for backwards compatibility.
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN') or os.environ.get('BROADWATCH_TELEGRAM_TOKEN', '')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID') or os.environ.get('BROADWATCH_TELEGRAM_CHAT_ID', '')
DISCORD_WEBHOOK = os.environ.get('BROADWATCH_DISCORD_WEBHOOK', '')
DISCORD_WEBHOOK_SUGGESTIONS = os.environ.get('BROADWATCH_DISCORD_SUGGESTIONS', '') or DISCORD_WEBHOOK

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
        'sound_path': os.environ.get('BROADWATCH_WICKED_SOUND', ''),
        'image_path': os.environ.get('BROADWATCH_WICKED_IMAGE', ''),
    },
    'les_mis': {
        'urls': [
            'https://miserableselmusical.es/',
            'https://miserableselmusical.es/elenco',
            'https://tickets.miserableselmusical.es/espectaculo/los-miserables/M01'
        ],
        'sound_path': os.environ.get('BROADWATCH_LESMIS_SOUND', ''),
        'image_path': os.environ.get('BROADWATCH_LESMIS_IMAGE', ''),
    },
    'tbom': {
        'urls': [
            'https://thebookofmormonelmusical.es',
            'https://thebookofmormonelmusical.es/elenco/',
            'https://tickets.thebookofmormonelmusical.es/espectaculo/the-book-of-mormon-el-musical/BM01'
        ],
        'sound_path': os.environ.get('BROADWATCH_TBOM_SOUND', ''),
        'image_path': os.environ.get('BROADWATCH_TBOM_IMAGE', ''),
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
_rate_limited_until = {}   # url -> timestamp hasta el que está rate-limited
start_time = time.time()
consecutive_failures = {url: 0 for url in URLS}
disabled_urls = set()


def create_session():
    """Create a requests session with retry strategy."""
    retry_strategy = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
    )
    adapter = requests.adapters.HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    # Sin User-Agent las webs de tickets nos bloquean como bot
    session.headers.update({
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Accept-Language': 'es-ES,es;q=0.9',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    })
    return session

SESSION = create_session()

def get_alert_assets(url):
    for musical, data in alerts_data.items():
        if url in data['urls']:
            return data['sound_path'], data['image_path']
    return '', ''


def send_telegram_alert(url, changes, timestamp, image_path):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️ Telegram credentials not configured; skipping Telegram alert.")
        return

    try:
        # Try to send photo (preferred) via sendPhoto
        if image_path and os.path.exists(image_path):
            try:
                with open(image_path, 'rb') as photo:
                    files = {'photo': photo}
                    data = {
                        'chat_id': CHAT_ID,
                        'caption': f"🎭 Ticket Update Detected:\n{url}\n{timestamp}"
                    }
                    resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=data, files=files, timeout=10)
                    if resp.ok:
                        print(f"✅ Telegram photo alert sent for {url}")
                        return
                    else:
                        print(f"⚠️ Telegram sendPhoto returned {resp.status_code}: {resp.text}")
            except Exception as e:
                print(f"⚠️ Telegram photo send failed: {e}")

        # Fallback to sendMessage with truncated changes
        msg = f"🎭 Ticket Update Detected\n{url}\n{timestamp}\n\nChanges:\n{changes[:1900]}"
        payload = {"chat_id": CHAT_ID, "text": msg}
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json=payload, timeout=10)
        if r.ok:
            print(f"📲 Telegram message sent for {url}")
        else:
            print(f"❌ Telegram send failed: {r.status_code} {r.text}")
    except Exception as e:
        print(f"❌ Failed to send Telegram alert: {e}")


try:
    import pywhatkit as kit
except Exception as e:
    print("pywhatkit import failed (continuando para debug):", e)
    kit = None

whatsapp_numbers = [n for n in [
    os.environ.get('BROADWATCH_WHATSAPP_1', ''),
    os.environ.get('BROADWATCH_WHATSAPP_2', ''),
] if n]  # Solo incluye números que estén configurados como variables de entorno

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


def send_discord_alert(url, changes, image_path=None):
    if not DISCORD_WEBHOOK:
        print("⚠️ Discord webhook not configured; skipping Discord alert.")
        return
    try:
        content = f"🎭 **Ticket Update Detected**\n{url}\n\nChanges:\n{changes[:1900]}"
        payload = {"content": content}
        # Post to Discord webhook
        resp = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            print("✅ Discord alert sent")
        else:
            print(f"❌ Discord webhook returned {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"❌ Failed to send Discord alert: {e}")

import websockets

async def notify_godot(url):
    async with websockets.connect("ws://localhost:8765") as ws:
        await ws.send(f"UPDATE:{url}")

import difflib
try:
    anthropic = importlib.import_module('anthropic')
except Exception:
    anthropic = None

old_contents = {url: "" for url in URLS}

# ── Claude summarizer ─────────────────────────────────────────────────────────
_anthropic_client = None

def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        if anthropic is None:
            return None
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if api_key:
            _anthropic_client = anthropic.Anthropic(api_key=api_key)
    return _anthropic_client


def summarize_diff(url, diff_text):
    """Llama a Claude para resumir un diff en lenguaje humano en español."""
    client = _get_anthropic_client()
    if not client:
        print("⚠️ ANTHROPIC_API_KEY no configurada — usando resumen genérico")
        return None

    truncated = diff_text[:3000] if len(diff_text) > 3000 else diff_text
    prompt = (
        "Eres el asistente de una web de seguimiento de musicales en España.\n"
        f"Se ha detectado un cambio en: {url}\n\n"
        f"Diff detectado:\n{truncated}\n\n"
        "Resume en UNA sola frase en español, clara y para fans de musicales, qué ha cambiado.\n"
        "Ejemplos: \'Se han actualizado los precios de Wicked\' / "
        "\'Nueva fecha para Los Miserables en abril\' / "
        "\'Cambio en el elenco de The Book of Mormon\'\n"
        "Responde SOLO con la frase, sin puntos ni comillas."
    )
    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}]
        )
        summary = message.content[0].text.strip().strip('"').strip("'")
        print(f"🤖 Resumen generado: {summary}")
        
        
        return summary
    except Exception as e:
        print(f"⚠️ Error llamando a Claude: {e}")
        return None

# Logs directory and manager
BASE_DIR = os.path.dirname(__file__)
LOG_DIR = Path(BASE_DIR) / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
MAX_LOG_ENTRIES = int(os.environ.get('BROADWATCH_MAX_LOG_ENTRIES', 10))


class LogManager:
    def __init__(self, log_dir: Path, max_entries: int = 10):
        self.log_dir = log_dir
        self.max_entries = max_entries
        self._cache = {}  # key -> list of entries (in-memory, avoids disk reads)

    def _path_for(self, key: str) -> Path:
        safe = key.replace('/', '_')
        return self.log_dir / f"{safe}.json"

    def load(self, key: str):
        if key in self._cache:
            return list(self._cache[key])
        p = self._path_for(key)
        if not p.exists():
            self._cache[key] = []
            return []
        try:
            with p.open('r', encoding='utf-8') as f:
                data = json.load(f)
                self._cache[key] = data
                return list(data)
        except Exception:
            self._cache[key] = []
            return []

    def save(self, key: str, entries):
        self._cache[key] = entries
        p = self._path_for(key)
        try:
            with p.open('w', encoding='utf-8') as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"❌ Error saving logs for {key}: {e}")

    def add(self, key: str, entry: dict):
        # Populate cache from disk on first access, then work in memory
        if key not in self._cache:
            self.load(key)
        entries = list(self._cache.get(key, []))
        entries.insert(0, entry)
        entries = entries[: self.max_entries]
        self.save(key, entries)
        # Also update a human-readable text version
        try:
            self._write_text_file(key, entries)
        except Exception as e:
            print(f"❌ Error writing text log for {key}: {e}")

    def get(self, key: str, n: int = None):
        n = n or self.max_entries
        entries = self.load(key)
        return entries[:n]

    def format_entries_to_text(self, entries):
        parts = []
        for e in entries:
            ts = e.get('timestamp', '')
            url = e.get('url', '')
            changes = e.get('changes', '')
            # Keep only first 1200 chars of changes for readability
            display = changes if len(changes) <= 1200 else changes[:1200] + '\n... (truncated)'
            parts.append(f"=== {ts} ===\nURL: {url}\n\nChanges:\n{display}\n\n")
        return "\n".join(parts)

    def _write_text_file(self, key: str, entries):
        p = self._path_for(key).with_suffix('.txt')
        text = self.format_entries_to_text(entries)
        try:
            with p.open('w', encoding='utf-8') as f:
                f.write(text)
        except Exception as e:
            print(f"❌ Error saving text log for {key}: {e}")


LOG_MANAGER = LogManager(LOG_DIR, max_entries=MAX_LOG_ENTRIES)

# Precomputed lookup: url -> monitor_key (avoids O(n) scan on every change)
_url_to_monitor_key = {
    url: key
    for key, data in alerts_data.items()
    for url in data.get('urls', [])
}


def get_monitor_key_for_url(url: str):
    if url in _url_to_monitor_key:
        return _url_to_monitor_key[url]
    # fallback: domain prefix match
    for mapped_url, key in _url_to_monitor_key.items():
        if mapped_url and mapped_url.split('/')[2] in url:
            return key
    return None

def find_differences(old_text, new_text):
    diff = difflib.unified_diff(old_text.splitlines(), new_text.splitlines(), lineterm="")
    return "\n".join(diff)

# Fichero donde se persisten los eventos visibles en la web
EVENTS_FILE = Path(BASE_DIR) / 'events.json'
MAX_EVENTS = 50
_events_cache = None  # in-memory cache; None means not yet loaded


def load_events():
    global _events_cache
    if _events_cache is not None:
        return list(_events_cache)
    if EVENTS_FILE.exists():
        try:
            _events_cache = json.loads(EVENTS_FILE.read_text(encoding='utf-8'))
            return list(_events_cache)
        except Exception:
            pass
    _events_cache = []
    return []


def save_event(monitor_key, url, summary, changes):
    """Guarda un evento legible en events.json para mostrarlo en la web."""
    global _events_cache
    musical_names = {
        'wicked': 'Wicked', 'les_mis': 'Los Miserables',
        'tbom': 'The Book of Mormon', 'houdini': 'Houdini',
    }
    event = {
        'id': f"{monitor_key}-{int(datetime.now(timezone.utc).timestamp())}",
        'monitor_key': monitor_key,
        'musical': musical_names.get(monitor_key, monitor_key.title()),
        'title': summary or f"Cambio detectado en {musical_names.get(monitor_key, monitor_key.title())}",
        'summary': summary or "Se ha detectado un cambio en la web oficial.",
        'url': url,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }
    events = load_events()
    events.insert(0, event)
    events = events[:MAX_EVENTS]
    _events_cache = events  # update cache before writing to disk
    try:
        EVENTS_FILE.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"💾 Evento guardado: {event['title']}")
    except Exception as e:
        print(f"\u274c Error guardando evento: {e}")
    return event


def notify_change(url, old_text, new_text):
    changes = find_differences(old_text, new_text)

    # 1. Pedir resumen legible a Claude
    summary = summarize_diff(url, changes)

    # 2. Notificacion de escritorio (solo en local con plyer disponible)
    short_msg = (summary or f"Cambio en {url}")[:250]
    if notification:
        try:
            notification.notify(title="Novedades BroadWatch", message=short_msg, timeout=10)
        except Exception:
            pass

    print(f"Cambio detectado en {url}")
    print(f"Resumen: {summary or '(sin resumen)'}")

    # 3. Guardar en logs tecnicos y en events.json para la web
    monitor_key = get_monitor_key_for_url(url) or 'general'
    try:
        entry = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'url': url,
            'summary': summary or '',
            'changes': changes,
        }
        LOG_MANAGER.add(monitor_key, entry)
    except Exception as e:
        print(f"Error saving log: {e}")

    save_event(monitor_key, url, summary, changes)

    # 4. Alertas externas con el resumen legible
    sound_path, image_path = get_alert_assets(url)
    try:
        winsound.PlaySound(sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass

    alert_text = summary or changes
    send_telegram_alert(url, alert_text, time.strftime("%Y-%m-%d %H:%M:%S"), image_path)
    for number in whatsapp_numbers:
        send_whatsapp_alert(number, url, alert_text)
    send_discord_alert(url, alert_text, image_path)


def get_page_content(url):
    # Si la URL está en rate-limit todavía, la saltamos sin hacer petición
    if _rate_limited_until.get(url, 0) > time.time():
        return ''

    try:
        response = SESSION.get(url, timeout=10)  # 10s es suficiente; antes 20s podía bloquear el pool

        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 60))
            _rate_limited_until[url] = time.time() + retry_after  # marca para reintento futuro
            print(f"⏳ Rate limited por {url} — reintento en {retry_after}s (sin bloquear el loop)")
            return ''

        response.raise_for_status()

        if not response.text.strip():
            raise ValueError("Empty response content")

        soup = BeautifulSoup(response.text, 'html.parser')
        for tag in soup(['script', 'style', 'noscript']):
            tag.decompose()
        return soup.get_text(separator=' ', strip=True)

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
            'urls_count': len(self.urls),
            # Campos que necesita la UI para mostrar el estado por URL
            'urls': list(self.urls),
            'consecutive_failures': dict(consecutive_failures),
            'disabled_urls': list(disabled_urls),
        }

    def run(self):
        print("▶️ Monitor started")
        while not self._stop_event.is_set():
            if self._pause_event.is_set():
                time.sleep(1)
                continue

            self.last_run = datetime.now(timezone.utc).isoformat()
            urls_to_check = [u for u in list(self.urls) if u not in disabled_urls]

            # Fetch todas las URLs en paralelo (max 3 simultáneas para no saturar servidores)
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = {executor.submit(get_page_content, u): u for u in urls_to_check}
                for future in as_completed(futures):
                    url = futures[future]
                    try:
                        content = future.result()
                    except Exception as e:
                        print(f"⚠️ Error inesperado fetching {url}: {e}")
                        content = ''

                    if not content:
                        consecutive_failures[url] = consecutive_failures.get(url, 0) + 1
                        if consecutive_failures[url] >= MAX_CONSECUTIVE_FAILURES:
                            disabled_urls.add(url)
                        continue

                    consecutive_failures[url] = 0

                    new_hash = hash_content(content)
                    if old_hashes.get(url) and new_hash == old_hashes[url]:
                        # Contenido idéntico — no hay nada que hacer
                        continue

                    if old_hashes.get(url):
                        # Hash distinto = cambio real
                        notify_change(url, old_contents.get(url, ''), content)

                    old_hashes[url] = new_hash
                    old_contents[url] = content

            reenable_disabled_urls()
            time.sleep(self.check_interval)


monitor = Monitor()

app = Flask(__name__)

# Sample events API data (used by the lightweight Flask UI below)
SAMPLE_EVENTS = [
    {
        'id': 'wicked',
        'monitor_key': 'wicked',
        'title': 'Wicked — El Musical',
        'place': 'Teatro Real, Madrid',
        'date': '2026-02-12 20:30',
        'summary': 'Un viaje musical por las historias que marcaron una generación.'
        ,'image': '/static/ui/images/wicked.jpg'
    },
    {
        'id': 'les_mis',
        'monitor_key': 'les_mis',
        'title': 'Los Miserables',
        'place': 'Teatro Nuevo, Barcelona',
        'date': '2026-02-14 19:00',
        'summary': 'Clásico musical en una nueva producción.'
        ,'image': '/static/ui/images/les-miserables.jpg'
    }
    
]



@app.route('/ping')
def ping():
    return jsonify({'status': 'ok'}), 200


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


# --- Events API: sirve eventos reales del monitor, con fallback a SAMPLE_EVENTS ---
@app.route('/api/events', methods=['GET'])
def api_events():
    real_events = load_events()
    resp = jsonify(real_events if real_events else SAMPLE_EVENTS)
    resp.headers['Cache-Control'] = 'public, max-age=30'
    return resp


@app.route('/api/events/<event_id>', methods=['GET'])
def api_event_get(event_id):
    all_events = load_events() or SAMPLE_EVENTS
    for e in all_events:
        if e['id'] == event_id:
            resp = jsonify(e)
            resp.headers['Cache-Control'] = 'public, max-age=30'
            return resp
    return jsonify({'error': 'not found'}), 404


@app.route('/api/logs/<monitor_key>', methods=['GET'])
def api_logs_for_key(monitor_key):
    try:
        n = int(request.args.get('n', MAX_LOG_ENTRIES))
    except Exception:
        n = MAX_LOG_ENTRIES
    logs = LOG_MANAGER.get(monitor_key, n)
    resp = jsonify({'ok': True, 'monitor_key': monitor_key, 'logs': logs})
    resp.headers['Cache-Control'] = 'public, max-age=30'
    return resp


@app.route('/api/logs', methods=['GET'])
def api_logs_by_url():
    url = request.args.get('url')
    if not url:
        return jsonify({'ok': False, 'error': 'missing url parameter'}), 400
    monitor_key = get_monitor_key_for_url(url) or 'general'
    try:
        n = int(request.args.get('n', MAX_LOG_ENTRIES))
    except Exception:
        n = MAX_LOG_ENTRIES
    logs = LOG_MANAGER.get(monitor_key, n)
    resp = jsonify({'ok': True, 'monitor_key': monitor_key, 'logs': logs})
    resp.headers['Cache-Control'] = 'public, max-age=30'
    return resp


@app.route('/api/logs/<monitor_key>/text', methods=['GET'])
def api_logs_for_key_text(monitor_key):
    try:
        n = int(request.args.get('n', MAX_LOG_ENTRIES))
    except Exception:
        n = MAX_LOG_ENTRIES
    entries = LOG_MANAGER.get(monitor_key, n)
    text = LOG_MANAGER.format_entries_to_text(entries)
    return Response(text, mimetype='text/plain; charset=utf-8')


@app.route('/api/logs/text', methods=['GET'])
def api_logs_by_url_text():
    url = request.args.get('url')
    if not url:
        return jsonify({'ok': False, 'error': 'missing url parameter'}), 400
    monitor_key = get_monitor_key_for_url(url) or 'general'
    try:
        n = int(request.args.get('n', MAX_LOG_ENTRIES))
    except Exception:
        n = MAX_LOG_ENTRIES
    entries = LOG_MANAGER.get(monitor_key, n)
    text = LOG_MANAGER.format_entries_to_text(entries)
    return Response(text, mimetype='text/plain; charset=utf-8')


@app.route('/api/notify', methods=['POST'])
def api_notify():
    data = request.get_json() or {}
    print(f"[/api/notify] received request: {data}")
    event_id = data.get('event_id')
    url = data.get('url')
    # Determine URL from event mapping if event_id provided
    if not url and event_id:
        for e in SAMPLE_EVENTS:
            if e.get('id') == event_id:
                monitor_key = e.get('monitor_key')
                if monitor_key and monitor_key in alerts_data:
                    url = alerts_data[monitor_key]['urls'][0]
                break

    if not url:
        # fallback to the first monitored URL
        url = URLS[0] if URLS else ''

    old = data.get('old', 'previous content')
    new = data.get('new', 'simulated change for test')

    # Run notification in background so HTTP request returns immediately
    def _bg():
        try:
            notify_change(url, old, new)
        except Exception as ex:
            print(f"Error running notify_change in bg: {ex}")

    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({'ok': True, 'url': url})


@app.route('/api/env', methods=['GET'])
def api_env():
    """Return non-sensitive presence flags for important env vars."""
    return jsonify({
        'telegram_token_set': bool(TELEGRAM_TOKEN),
        'telegram_chat_id_set': bool(CHAT_ID),
        'discord_webhook_set': bool(DISCORD_WEBHOOK),
        'twilio_configured': bool(account_sid and auth_token)
    })


@app.route('/api/test_telegram', methods=['POST', 'GET'])
def api_test_telegram():
    """Send a simple Telegram test message using configured credentials."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return jsonify({'ok': False, 'error': 'telegram credentials not configured'}), 400
    try:
        payload = {'chat_id': CHAT_ID, 'text': '📣 Prueba de BroadWatch: mensaje de comprobación.'}
        resp = requests.post(f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage', json=payload, timeout=10)
        return jsonify({'ok': resp.ok, 'status_code': resp.status_code, 'text': resp.text})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


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


SUGGESTIONS_FILE = Path(BASE_DIR) / 'suggestions.json'

def load_suggestions():
    if SUGGESTIONS_FILE.exists():
        try:
            return json.loads(SUGGESTIONS_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return []

def save_suggestion(entry):
    suggestions = load_suggestions()
    suggestions.insert(0, entry)
    try:
        SUGGESTIONS_FILE.write_text(json.dumps(suggestions, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        print(f"❌ Error guardando sugerencia: {e}")


def classify_suggestion(message, musical, name):
    """Usa Haiku para clasificar la sugerencia y generar agradecimiento personalizado."""
    client = _get_anthropic_client()
    if not client:
        return 'general', '¡Gracias por tu sugerencia!'
    prompt = (
        "Eres el asistente de BroadWatch, una web de musicales en España.\n"
        f"Un usuario {'llamado ' + name if name else 'anónimo'} ha enviado esta sugerencia"
        f"{' sobre ' + musical if musical else ''}:\n\"{message}\"\n\n"
        "1. Clasifícala en UNA de estas categorías: petición_musical, mejora_web, bug, elenco, precios, calendario, otro\n"
        "2. Escribe UNA frase de agradecimiento personalizada y cercana (tuteo, tono fan).\n"
        "Responde SOLO en formato JSON: {\"categoria\": \"...\", \"gracias\": \"...\"}"
    )
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        result = json.loads(msg.content[0].text.strip())
        return result.get('categoria', 'otro'), result.get('gracias', '¡Gracias por tu sugerencia!')
    except Exception as e:
        print(f"⚠️ Error clasificando sugerencia con Haiku: {e}")
        return 'otro', '¡Gracias por tu sugerencia!'


def send_suggestion_to_discord(entry, categoria):
    if not DISCORD_WEBHOOK_SUGGESTIONS:
        print("⚠️ Discord suggestions webhook no configurado.")
        return
    category_emojis = {
        'petición_musical': '🎭', 'mejora_web': '💡', 'bug': '🐛',
        'elenco': '🎤', 'precios': '💰', 'calendario': '📅', 'otro': '📬'
    }
    emoji = category_emojis.get(categoria, '📬')
    musical_str = entry.get('musical') or '—'
    name_str = entry.get('name') or 'Anónimo'
    email_str = entry.get('email') or '—'
    content = (
        f"{emoji} **Nueva sugerencia — {categoria.replace('_', ' ').title()}**\n"
        f"👤 **De:** {name_str}  |  ✉️ {email_str}\n"
        f"🎵 **Musical:** {musical_str}\n"
        f"💬 **Mensaje:** {entry.get('message', '')}\n"
        f"🕐 {entry.get('timestamp', '')}"
    )
    try:
        resp = requests.post(DISCORD_WEBHOOK_SUGGESTIONS, json={"content": content}, timeout=10)
        if resp.status_code in (200, 204):
            print(f"✅ Sugerencia enviada a Discord ({categoria})")
        else:
            print(f"❌ Discord suggestions returned {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"❌ Error enviando sugerencia a Discord: {e}")


@app.route('/api/suggestions', methods=['POST'])
def api_suggestions():
    data = request.get_json() or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'ok': False, 'error': 'mensaje vacío'}), 400

    name    = (data.get('name')    or '').strip()[:80]
    email   = (data.get('email')   or '').strip()[:120]
    musical = (data.get('musical') or '').strip()[:50]

    categoria, gracias = classify_suggestion(message, musical, name)

    entry = {
        'id':        f"sg-{int(datetime.now(timezone.utc).timestamp())}",
        'name':      name,
        'email':     email,
        'musical':   musical,
        'message':   message[:1000],
        'categoria': categoria,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }
    save_suggestion(entry)

    threading.Thread(target=send_suggestion_to_discord, args=(entry, categoria), daemon=True).start()

    return jsonify({'ok': True, 'categoria': categoria, 'gracias': gracias})


# ── BUSCA PLAN ────────────────────────────────────────────────────────────────

COMPANIONS_FILE = Path(BASE_DIR) / 'companions.json'

MUSICAL_NAMES = {
    'wicked': 'Wicked',
    'lesmis': 'Los Miserables',
    'tbom': 'The Book of Mormon',
    'houdini': 'Houdini',
}

def load_companions():
    if COMPANIONS_FILE.exists():
        try:
            data = json.loads(COMPANIONS_FILE.read_text(encoding='utf-8'))
            now = datetime.now(timezone.utc).isoformat()
            return [c for c in data if c.get('expires', '') > now]
        except Exception:
            pass
    return []

def save_companions(companions):
    try:
        COMPANIONS_FILE.write_text(json.dumps(companions, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        print(f"❌ Error guardando companions: {e}")

def send_companion_to_discord(entry):
    if not DISCORD_WEBHOOK_SUGGESTIONS:
        return
    musical_name = MUSICAL_NAMES.get(entry.get('musical', ''), entry.get('musical', '—'))
    contact_icons = {'telegram': '✈️', 'email': '✉️', 'instagram': '📸', 'twitter': '🐦', 'otro': '💬'}
    icon = contact_icons.get(entry.get('contact_type', 'otro'), '💬')
    content = (
        f"🎟️ **Nueva búsqueda de plan — {musical_name}**\n"
        f"👤 **{entry.get('name') or 'Anónimo'}**  ·  🗓️ {entry.get('date', '—')} a las {entry.get('time', '—')}\n"
        f"🪑 **Entradas disponibles:** {entry.get('seats', 1)}\n"
        f"💬 {entry.get('message', '')}\n"
        f"{icon} **Contacto:** {entry.get('contact', '—')} ({entry.get('contact_type', '')})"
    )
    try:
        requests.post(DISCORD_WEBHOOK_SUGGESTIONS, json={"content": content}, timeout=10)
    except Exception as e:
        print(f"❌ Error enviando companion a Discord: {e}")

@app.route('/api/companions', methods=['GET'])
def api_companions_get():
    musical = request.args.get('musical', '')
    companions = load_companions()
    if musical:
        companions = [c for c in companions if c.get('musical') == musical]
    companions.sort(key=lambda c: c.get('date', ''), reverse=False)
    return jsonify(companions)

@app.route('/api/companions', methods=['POST'])
def api_companions_post():
    data = request.get_json() or {}
    name    = (data.get('name')    or '').strip()[:80]
    musical = (data.get('musical') or '').strip()[:20]
    date    = (data.get('date')    or '').strip()[:10]   # YYYY-MM-DD
    time    = (data.get('time')    or '').strip()[:5]    # HH:MM
    seats   = max(1, min(10, int(data.get('seats', 1) or 1)))
    message = (data.get('message') or '').strip()[:500]
    contact = (data.get('contact') or '').strip()[:100]
    contact_type = (data.get('contact_type') or 'otro').strip()[:20]

    if not musical or not date or not message or not contact:
        return jsonify({'ok': False, 'error': 'Faltan campos obligatorios'}), 400

    # La publicación expira 3 horas después de la función
    try:
        show_dt = datetime.strptime(f"{date} {time or '23:59'}", "%Y-%m-%d %H:%M")
        expires = show_dt.replace(tzinfo=timezone.utc) + __import__('datetime').timedelta(hours=3)
        expires_iso = expires.isoformat()
    except Exception:
        return jsonify({'ok': False, 'error': 'Fecha inválida'}), 400

    entry = {
        'id':           f"cp-{int(datetime.now(timezone.utc).timestamp())}",
        'name':         name or 'Anónimo',
        'musical':      musical,
        'date':         date,
        'time':         time,
        'seats':        seats,
        'message':      message,
        'contact':      contact,
        'contact_type': contact_type,
        'timestamp':    datetime.now(timezone.utc).isoformat(),
        'expires':      expires_iso,
    }

    companions = load_companions()
    companions.insert(0, entry)
    save_companions(companions)

    threading.Thread(target=send_companion_to_discord, args=(entry,), daemon=True).start()

    return jsonify({'ok': True})


if __name__ == '__main__':
    port = int(os.environ.get('BROADWATCH_PORT', 8080))
    try:
        app.run(host='0.0.0.0', port=port)
    except KeyboardInterrupt:
        print("\n👋 Script detenido manualmente. ¡Hasta pronto!")