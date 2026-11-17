import telebot
import subprocess
import os
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import json
import logging
import signal
import threading
import re
import sys
import atexit
import requests
import gc
import resource
import multiprocessing
import venv
import random
import string

from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for
from threading import Thread

# ============================================================
# === CPU / RAM OPTİMİZASYON (Render uyumlu) ===
# ============================================================
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('NUMEXPR_NUM_THREADS', '1')
os.environ.setdefault('PYTHONUNBUFFERED', '1')

# Alt süreç RAM limiti (MB) - Render free için 150 önerilir
CHILD_RAM_LIMIT_MB = 180
CHILD_CPU_NICE = 19

# ============================================================
# === FLASK WEB PANEL ===
# ============================================================
app = Flask('')
app.secret_key = "rinex_super_secret_key_change_me_12345"

# Web panel şifresi (Render'da env ile değiştir)
WEB_ADMIN_USER = os.environ.get("WEB_ADMIN_USER", "admin")
WEB_ADMIN_PASS = os.environ.get("WEB_ADMIN_PASS", "rinex2024")

# ============================================================
# === BOT AYARLARI ===
# ============================================================
BOT_START_TIME = datetime.now()

# ZORUNLU KANALLAR
REQUIRED_CHANNELS = ["@suprasorgu", "@logsuzlarpanelvip"]
REQUIRED_CHANNEL_URLS = ["https://t.me/suprasorgu", "https://t.me/logsuzlarpanelvip"]

def get_uptime():
    uptime = datetime.now() - BOT_START_TIME
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}g {hours}s {minutes}d {seconds}sn"

# === BUTON STİLİ (BTN_STYLE) - Tüm uzantılarda geçerli ===
BTN_STYLE = {
    "onay": "✅ Onayla",
    "red": "❌ Reddet",
    "iptal": "🔙 İptal",
    "yukle": "📤 Dosya Yükle",
    "kontrol": "📂 Dosyalarım",
    "hiz": "⚡ Bot Hızı",
    "istat": "📊 İstatistik",
    "iletisim": "📞 İletişim",
    "uptime": "⏱️ Çalışma Süresi",
    "ai": "🤖 MPX AI",
    "kanal": "📢 Kanallar",
    "admin": "👑 Admin Panel",
    "abonelik": "💳 Abonelikler",
    "yayin": "📢 Yayın",
    "kilit": "🔒 Kilitle",
    "kilit_ac": "🔓 Kilidi Aç",
    "tumu": "🟢 Tüm Scriptleri Çalıştır",
    "geri": "🔙 Ana Menü",
    "sanal": "🐍 Sanal Ortam Botları",
    "cpu": "💻 Sistem Durumu",
}

TOKEN = '9175505754:AAHkwodgoxPt4tM3FngJwqCGCwNWmm9cyQs'
OWNER_ID =8764246995
ADMIN_ID =8764246995
YOUR_USERNAME = '@suprapanel'
UPDATE_CHANNEL = 'https://t.me/suprapanel'

A4F_API_URL = "https://samuraiapi.in/v1/chat/completions"
A4F_API_KEY = "sk-NK6SS9tpWghyFJwkZLoCis1sMaF6"
A4F_MODEL = "provider10-claude-sonnet-4-20250514(clinesp)"

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
VENV_DIR = os.path.join(BASE_DIR, 'venvs')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

FREE_USER_LIMIT = 2
SUBSCRIBED_USER_LIMIT = 2
ADMIN_LIMIT = 20
OWNER_LIMIT = float('inf')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)
os.makedirs(VENV_DIR, exist_ok=True)

bot = telebot.TeleBot(TOKEN)

bot_scripts = {}
user_subscriptions = {}
user_files = {}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False

# ONAY BEKLEYEN DOSYALAR: {file_key: {...}}
pending_approvals = {}

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============================================================
# === VERİTABANI ===
# ============================================================
def init_db():
    logger.info(f"Veritabanı başlatılıyor: {DATABASE_PATH}")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA synchronous=NORMAL')
        c.execute('PRAGMA cache_size=-2000')
        c.execute('PRAGMA temp_store=MEMORY')
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                     (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_files
                     (user_id INTEGER, file_name TEXT, file_type TEXT,
                      PRIMARY KEY (user_id, file_name))''')
        c.execute('''CREATE TABLE IF NOT EXISTS active_users
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        conn.commit()
        conn.close()
        logger.info("Veritabanı hazır.")
    except Exception as e:
        logger.error(f"DB başlatma hatası: {e}", exc_info=True)

def load_data():
    logger.info("Veriler yükleniyor...")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT user_id, expiry FROM subscriptions')
        for user_id, expiry in c.fetchall():
            try:
                user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
            except ValueError:
                pass
        c.execute('SELECT user_id, file_name, file_type FROM user_files')
        for user_id, file_name, file_type in c.fetchall():
            if user_id not in user_files:
                user_files[user_id] = []
            user_files[user_id].append((file_name, file_type))
        c.execute('SELECT user_id FROM active_users')
        active_users.update(user_id for (user_id,) in c.fetchall())
        c.execute('SELECT user_id FROM admins')
        admin_ids.update(user_id for (user_id,) in c.fetchall())
        conn.close()
        logger.info(f"Yüklendi: {len(active_users)} kullanıcı, {len(user_subscriptions)} abonelik.")
    except Exception as e:
        logger.error(f"Veri yükleme hatası: {e}", exc_info=True)

init_db()
load_data()

# ============================================================
# === YARDIMCI FONKSİYONLAR ===
# ============================================================
def get_user_folder(user_id):
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

def get_user_venv(user_id):
    """Her kullanıcı için sanal ortam (venv) oluştur."""
    venv_path = os.path.join(VENV_DIR, str(user_id))
    python_exe = os.path.join(venv_path, 'bin', 'python') if os.name != 'nt' else os.path.join(venv_path, 'Scripts', 'python.exe')
    if not os.path.exists(python_exe):
        try:
            logger.info(f"Venv oluşturuluyor: {venv_path}")
            venv.create(venv_path, with_pip=True, clear=False)
            # pip upgrade (sessiz)
            try:
                subprocess.run([python_exe, '-m', 'pip', 'install', '--upgrade', 'pip', '-q'],
                               timeout=120, capture_output=True)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Venv oluşturma hatası {venv_path}: {e}")
            return sys.executable
    return python_exe

def get_user_file_limit(user_id):
    if user_id == OWNER_ID: return OWNER_LIMIT
    if user_id in admin_ids: return ADMIN_LIMIT
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT

def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))

def is_bot_running(script_owner_id, file_name):
    script_key = f"{script_owner_id}_{file_name}"
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            is_running = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
            if not is_running:
                if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                    try: script_info['log_file'].close()
                    except: pass
                if script_key in bot_scripts:
                    del bot_scripts[script_key]
            return is_running
        except psutil.NoSuchProcess:
            if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                try: script_info['log_file'].close()
                except: pass
            if script_key in bot_scripts:
                del bot_scripts[script_key]
            return False
        except Exception as e:
            logger.error(f"Process kontrol hatası {script_key}: {e}")
            return False
    return False

def _apply_child_limits():
    """Alt süreç RAM ve CPU limiti (Linux)."""
    try:
        if os.name != 'nt':
            limit_bytes = CHILD_RAM_LIMIT_MB * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    except Exception:
        pass

def _get_low_priority_popen_args():
    """CPU düşük öncelik argümanları."""
    kwargs = {}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW
    else:
        cmd_prefix = ['nice', '-n', str(CHILD_CPU_NICE)]
        try:
            if shutil.which('taskset') and multiprocessing.cpu_count() > 1:
                cmd_prefix = ['taskset', '-c', '0', 'nice', '-n', str(CHILD_CPU_NICE)]
        except Exception:
            pass
        kwargs['_cmd_prefix'] = cmd_prefix
    return kwargs

def kill_process_tree(process_info):
    pid = None
    script_key = process_info.get('script_key', 'N/A')
    try:
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close') and not process_info['log_file'].closed:
            try: process_info['log_file'].close()
            except: pass
        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
            pid = process.pid
            if pid:
                try:
                    parent = psutil.Process(pid)
                    children = parent.children(recursive=True)
                    for child in children:
                        try: child.terminate()
                        except: 
                            try: child.kill()
                            except: pass
                    try:
                        parent.terminate()
                        try: parent.wait(timeout=1)
                        except psutil.TimeoutExpired: parent.kill()
                    except psutil.NoSuchProcess: pass
                except psutil.NoSuchProcess: pass
    except Exception as e:
        logger.error(f"Kill hatası {pid or 'N/A'} ({script_key}): {e}")

# ============================================================
# === SİSTEM BİLGİSİ (CPU/RAM) ===
# ============================================================
def get_system_stats():
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage(BASE_DIR)
        return {
            "cpu_percent": cpu,
            "ram_percent": ram.percent,
            "ram_used_mb": round(ram.used / (1024*1024), 1),
            "ram_total_mb": round(ram.total / (1024*1024), 1),
            "disk_percent": disk.percent,
            "disk_used_gb": round(disk.used / (1024**3), 2),
            "disk_total_gb": round(disk.total / (1024**3), 2),
            "uptime": get_uptime(),
            "running_bots": len(bot_scripts),
            "pending_approvals": len(pending_approvals),
        }
    except Exception as e:
        logger.error(f"Sistem istatistik hatası: {e}")
        return {}

# ============================================================
# === DB KAYIT FONKSİYONLARI ===
# ============================================================
DB_LOCK = threading.Lock()

def save_user_file(user_id, file_name, file_type='py'):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type) VALUES (?, ?, ?)',
                      (user_id, file_name, file_type))
            conn.commit()
            if user_id not in user_files: user_files[user_id] = []
            user_files[user_id] = [(fn, ft) for fn, ft in user_files[user_id] if fn != file_name]
            user_files[user_id].append((file_name, file_type))
        except Exception as e:
            logger.error(f"Dosya kaydetme hatası: {e}")
        finally: conn.close()

def remove_user_file_db(user_id, file_name):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
            conn.commit()
            if user_id in user_files:
                user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
                if not user_files[user_id]: del user_files[user_id]
        except Exception as e:
            logger.error(f"Dosya silme hatası: {e}")
        finally: conn.close()

def add_active_user(user_id):
    active_users.add(user_id)
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Aktif kullanıcı hatası: {e}")
        finally: conn.close()

def save_subscription(user_id, expiry):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)', (user_id, expiry.isoformat()))
            conn.commit()
            user_subscriptions[user_id] = {'expiry': expiry}
        except Exception as e:
            logger.error(f"Abonelik kaydetme hatası: {e}")
        finally: conn.close()

def remove_subscription_db(user_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
            conn.commit()
            if user_id in user_subscriptions: del user_subscriptions[user_id]
        except Exception as e:
            logger.error(f"Abonelik silme hatası: {e}")
        finally: conn.close()

def add_admin_db(admin_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
            conn.commit()
            admin_ids.add(admin_id)
        except Exception as e:
            logger.error(f"Admin ekleme hatası: {e}")
        finally: conn.close()

def remove_admin_db(admin_id):
    if admin_id == OWNER_ID: return False
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM admins WHERE user_id = ?', (admin_id,))
            conn.commit()
            admin_ids.discard(admin_id)
            return True
        except Exception as e:
            logger.error(f"Admin silme hatası: {e}")
            return False
        finally: conn.close()

# ============================================================
# === ZORUNLU KANAL KONTROL ===
# ============================================================
def check_required_channels(user_id):
    """Kullanıcı tüm zorunlu kanallara üye mi?"""
    if user_id in admin_ids:
        return True, []
    missing = []
    for ch in REQUIRED_CHANNELS:
        try:
            member = bot.get_chat_member(ch, user_id)
            if member.status in ['left', 'kicked']:
                missing.append(ch)
        except Exception as e:
            logger.warning(f"Kanal kontrol hatası {ch} - {user_id}: {e}")
            missing.append(ch)
    return len(missing) == 0, missing

def get_join_markup(missing_channels):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for ch in missing_channels:
        url = f"https://t.me/{ch.replace('@', '')}"
        markup.add(types.InlineKeyboardButton(f"📢 {ch}", url=url))
    markup.add(types.InlineKeyboardButton(BTN_STYLE["onay"] + " Üyeyim", callback_data='check_join'))
    return markup

# ============================================================
# === MENÜLER ===
# ============================================================
def create_main_menu_inline(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton(BTN_STYLE["kanal"], callback_data='channels'),
        types.InlineKeyboardButton(BTN_STYLE["yukle"], callback_data='upload'),
        types.InlineKeyboardButton(BTN_STYLE["kontrol"], callback_data='check_files'),
        types.InlineKeyboardButton(BTN_STYLE["hiz"], callback_data='speed'),
        types.InlineKeyboardButton(BTN_STYLE["istat"], callback_data='stats'),
        types.InlineKeyboardButton(BTN_STYLE["iletisim"], url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'),
        types.InlineKeyboardButton(BTN_STYLE["ai"], callback_data='mpx_ai'),
    ]
    if user_id in admin_ids:
        buttons.extend([
            types.InlineKeyboardButton(BTN_STYLE["abonelik"], callback_data='subscription'),
            types.InlineKeyboardButton(BTN_STYLE["yayin"], callback_data='broadcast'),
            types.InlineKeyboardButton(BTN_STYLE["kilit"] if not bot_locked else BTN_STYLE["kilit_ac"],
                                       callback_data='lock_bot' if not bot_locked else 'unlock_bot'),
            types.InlineKeyboardButton(BTN_STYLE["admin"], callback_data='admin_panel'),
            types.InlineKeyboardButton(BTN_STYLE["tumu"], callback_data='run_all_scripts'),
            types.InlineKeyboardButton(BTN_STYLE["sanal"], callback_data='venv_bots'),
            types.InlineKeyboardButton(BTN_STYLE["cpu"], callback_data='sys_stats'),
        ])
    # 2'li satırlar
    for i in range(0, len(buttons) - 1, 2):
        markup.add(buttons[i], buttons[i+1])
    if len(buttons) % 2 == 1:
        markup.add(buttons[-1])
    markup.add(types.InlineKeyboardButton(BTN_STYLE["uptime"], callback_data='uptime'))
    return markup

def create_reply_keyboard_main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    if user_id in admin_ids:
        layout = [
            [BTN_STYLE["kanal"], BTN_STYLE["uptime"]],
            [BTN_STYLE["yukle"], BTN_STYLE["kontrol"]],
            [BTN_STYLE["hiz"], BTN_STYLE["istat"]],
            [BTN_STYLE["abonelik"], BTN_STYLE["yayin"]],
            [BTN_STYLE["kilit"] if not bot_locked else BTN_STYLE["kilit_ac"], BTN_STYLE["tumu"]],
            [BTN_STYLE["admin"], BTN_STYLE["iletisim"]],
            [BTN_STYLE["ai"], BTN_STYLE["sanal"]],
            [BTN_STYLE["cpu"]],
        ]
    else:
        layout = [
            [BTN_STYLE["kanal"], BTN_STYLE["uptime"]],
            [BTN_STYLE["yukle"], BTN_STYLE["kontrol"]],
            [BTN_STYLE["hiz"], BTN_STYLE["istat"]],
            [BTN_STYLE["iletisim"], BTN_STYLE["ai"]],
        ]
    for row in layout:
        markup.add(*[types.KeyboardButton(text) for text in row])
    return markup

def create_control_buttons(script_owner_id, file_name, is_running=True):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.row(
            types.InlineKeyboardButton("🔴 Durdur", callback_data=f'stop_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🔄 Yeniden Başlat", callback_data=f'restart_{script_owner_id}_{file_name}')
        )
    else:
        markup.row(
            types.InlineKeyboardButton("🟢 Başlat", callback_data=f'start_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🗑️ Sil", callback_data=f'delete_{script_owner_id}_{file_name}')
        )
    markup.row(
        types.InlineKeyboardButton("📜 Loglar", callback_data=f'logs_{script_owner_id}_{file_name}'),
        types.InlineKeyboardButton(BTN_STYLE["geri"], callback_data='check_files')
    )
    return markup

def create_admin_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Admin Ekle', callback_data='add_admin'),
        types.InlineKeyboardButton('➖ Admin Çıkar', callback_data='remove_admin')
    )
    markup.row(types.InlineKeyboardButton('📋 Admin Listesi', callback_data='list_admins'))
    markup.row(types.InlineKeyboardButton(BTN_STYLE["geri"], callback_data='back_to_main'))
    return markup

def create_subscription_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Abonelik Ekle', callback_data='add_subscription'),
        types.InlineKeyboardButton('➖ Abonelik Sil', callback_data='remove_subscription')
    )
    markup.row(types.InlineKeyboardButton('🔍 Abonelik Kontrol', callback_data='check_subscription'))
    markup.row(types.InlineKeyboardButton(BTN_STYLE["geri"], callback_data='back_to_main'))
    return markup

def create_approval_buttons(file_key):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton(BTN_STYLE["onay"], callback_data=f'approve_{file_key}'),
        types.InlineKeyboardButton(BTN_STYLE["red"], callback_data=f'reject_{file_key}')
    )
    return markup

# ============================================================
# === SCRIPT ÇALIŞTIRMA (VENV + ONAY SİSTEMİ) ===
# ============================================================
TELEGRAM_MODULES = {
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'aiogram': 'aiogram',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'bs4': 'beautifulsoup4',
    'requests': 'requests',
    'pillow': 'Pillow',
    'PIL': 'Pillow',
    'cv2': 'opencv-python',
    'yaml': 'PyYAML',
    'dotenv': 'python-dotenv',
    'dateutil': 'python-dateutil',
    'pandas': 'pandas',
    'numpy': 'numpy',
    'flask': 'Flask',
    'django': 'Django',
    'sqlalchemy': 'SQLAlchemy',
    'psutil': 'psutil',
    'asyncio': None, 'json': None, 'datetime': None, 'os': None, 'sys': None,
    're': None, 'time': None, 'math': None, 'random': None, 'logging': None,
    'threading': None, 'subprocess': None, 'zipfile': None, 'tempfile': None,
    'shutil': None, 'sqlite3': None, 'atexit': None,
}

def attempt_install_pip(module_name, message, user_id):
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name)
    if package_name is None:
        return True  # core module
    try:
        python_exe = get_user_venv(user_id)
        bot.reply_to(message, f"📦 `{module_name}` bulunamadı. Sanal ortama kuruluyor...", parse_mode='Markdown')
        result = subprocess.run([python_exe, '-m', 'pip', 'install', package_name, '-q'],
                                capture_output=True, text=True, timeout=180)
        if result.returncode == 0:
            bot.reply_to(message, f"✅ `{package_name}` kuruldu.", parse_mode='Markdown')
            return True
        else:
            bot.reply_to(message, f"❌ Kurulum başarısız:\n```\n{(result.stderr or '')[:800]}\n```", parse_mode='Markdown')
            return False
    except Exception as e:
        logger.error(f"Pip kurulum hatası: {e}")
        bot.reply_to(message, f"❌ Kurulum hatası: {e}")
        return False

def run_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=1):
    """Python script çalıştır (venv içinde)."""
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj_for_reply, f"❌ `{file_name}` {max_attempts} denemede başlatılamadı.")
        return
    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"Python çalıştırma denemesi {attempt}: {script_path}")
    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj_for_reply, f"❌ Script bulunamadı: `{file_name}`")
            return

        python_exe = get_user_venv(script_owner_id)

        # Pre-check
        if attempt == 1:
            check_proc = None
            try:
                check_proc = subprocess.Popen([python_exe, script_path], cwd=user_folder,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              text=True, encoding='utf-8', errors='ignore')
                stdout, stderr = check_proc.communicate(timeout=5)
                if check_proc.returncode != 0 and stderr:
                    match = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                    if match:
                        module_name = match.group(1).strip().strip("'\"")
                        if attempt_install_pip(module_name, message_obj_for_reply, script_owner_id):
                            bot.reply_to(message_obj_for_reply, f"🔄 Yeniden deneniyor...")
                            time.sleep(0.3)
                            threading.Thread(target=run_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt + 1)).start()
                            return
                    bot.reply_to(message_obj_for_reply, f"❌ Script hatası:\n```\n{stderr[:500]}\n```", parse_mode='Markdown')
                    return
            except subprocess.TimeoutExpired:
                if check_proc and check_proc.poll() is None:
                    check_proc.kill(); check_proc.communicate()
            except Exception as e:
                logger.error(f"Pre-check hatası: {e}")
            finally:
                if check_proc and check_proc.poll() is None:
                    check_proc.kill(); check_proc.communicate()

        # Uzun süreli çalıştırma
        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        try: log_file = open(log_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
            bot.reply_to(message_obj_for_reply, f"❌ Log dosyası açılamadı: {e}")
            return
        try:
            priority_args = _get_low_priority_popen_args()
            cmd_prefix = priority_args.pop('_cmd_prefix', [])
            cmd = cmd_prefix + [python_exe, script_path]
            process = subprocess.Popen(
                cmd, cwd=user_folder, stdout=log_file, stderr=log_file,
                stdin=subprocess.DEVNULL,
                preexec_fn=_apply_child_limits if os.name != 'nt' else None,
                **priority_args
            )
            bot_scripts[script_key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj_for_reply.chat.id,
                'script_owner_id': script_owner_id,
                'start_time': datetime.now(), 'user_folder': user_folder,
                'type': 'py', 'script_key': script_key
            }
            bot.reply_to(message_obj_for_reply, f"✅ Python script `{file_name}` çalışıyor! (PID: {process.pid})\n👤 Kullanıcı: `{script_owner_id}`", parse_mode='Markdown')
        except Exception as e:
            if log_file and not log_file.closed: log_file.close()
            bot.reply_to(message_obj_for_reply, f"❌ Çalıştırma hatası: {e}")
            if script_key in bot_scripts: del bot_scripts[script_key]
    except Exception as e:
        logger.error(f"run_script hatası: {e}", exc_info=True)
        bot.reply_to(message_obj_for_reply, f"❌ Beklenmeyen hata: {e}")
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]

def run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=1):
    """JS script çalıştır (node)."""
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj_for_reply, f"❌ `{file_name}` başlatılamadı.")
        return
    script_key = f"{script_owner_id}_{file_name}"
    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj_for_reply, f"❌ Script bulunamadı: `{file_name}`")
            return
        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        try: log_file = open(log_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
            bot.reply_to(message_obj_for_reply, f"❌ Log dosyası açılamadı: {e}")
            return
        try:
            priority_args = _get_low_priority_popen_args()
            cmd_prefix = priority_args.pop('_cmd_prefix', [])
            cmd = cmd_prefix + ['node', script_path]
            process = subprocess.Popen(
                cmd, cwd=user_folder, stdout=log_file, stderr=log_file,
                stdin=subprocess.DEVNULL,
                preexec_fn=_apply_child_limits if os.name != 'nt' else None,
                **priority_args
            )
            bot_scripts[script_key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj_for_reply.chat.id,
                'script_owner_id': script_owner_id,
                'start_time': datetime.now(), 'user_folder': user_folder,
                'type': 'js', 'script_key': script_key
            }
            bot.reply_to(message_obj_for_reply, f"✅ JS script `{file_name}` çalışıyor! (PID: {process.pid})")
        except FileNotFoundError:
            if log_file and not log_file.closed: log_file.close()
            bot.reply_to(message_obj_for_reply, "❌ 'node' bulunamadı! Node.js kurun.")
            if script_key in bot_scripts: del bot_scripts[script_key]
        except Exception as e:
            if log_file and not log_file.closed: log_file.close()
            bot.reply_to(message_obj_for_reply, f"❌ Çalıştırma hatası: {e}")
            if script_key in bot_scripts: del bot_scripts[script_key]
    except Exception as e:
        logger.error(f"run_js_script hatası: {e}")
        bot.reply_to(message_obj_for_reply, f"❌ Beklenmeyen hata: {e}")

# ============================================================
# === ONAY SİSTEMİ ===
# ============================================================
def send_for_approval(user_id, file_name, file_type, file_path, message):
    """Dosyayı admin'e onay için gönder."""
    file_key = f"{user_id}_{int(time.time())}_{random.randint(1000,9999)}"
    pending_approvals[file_key] = {
        'user_id': user_id,
        'file_name': file_name,
        'file_type': file_type,
        'file_path': file_path,
        'chat_id': message.chat.id,
        'timestamp': datetime.now(),
        'message_id': message.message_id
    }
    user_name = message.from_user.first_name
    user_username = message.from_user.username or "N/A"
    caption = (
        f"🔔 **YENİ DOSYA ONAYI**\n\n"
        f"👤 Kullanıcı: {user_name}\n"
        f"🆔 ID: `{user_id}`\n"
        f"📱 Kullanıcı Adı: @{user_username}\n"
        f"📄 Dosya: `{file_name}`\n"
        f"📦 Tür: {file_type}\n"
        f"⏰ Zaman: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n\n"
        f"⚠️ Onaylamadan çalıştırılmayacak!"
    )
    try:
        bot.send_message(OWNER_ID, caption, parse_mode='Markdown', reply_markup=create_approval_buttons(file_key))
        # Dosyayı da admin'e gönder (inceleme için)
        try:
            with open(file_path, 'rb') as f:
                bot.send_document(OWNER_ID, f, caption=f"📎 `{file_name}` - İnceleme için")
        except Exception as e:
            logger.warning(f"Dosya gönderilemedi: {e}")
        bot.reply_to(message, f"⏳ Dosyanız admin onayına gönderildi.\n📄 `{file_name}`\n\nOnaylandığında bilgilendirileceksiniz.", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Onay gönderme hatası: {e}")
        bot.reply_to(message, f"❌ Onay gönderilemedi: {e}")

def handle_zip_file(downloaded_file_content, file_name_zip, message):
    user_id = message.from_user.id
    user_folder = get_user_folder(user_id)
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
        zip_path = os.path.join(temp_dir, file_name_zip)
        with open(zip_path, 'wb') as f: f.write(downloaded_file_content)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for member in zip_ref.infolist():
                member_path = os.path.abspath(os.path.join(temp_dir, member.filename))
                if not member_path.startswith(os.path.abspath(temp_dir)):
                    raise zipfile.BadZipFile(f"Güvensiz yol: {member.filename}")
            zip_ref.extractall(temp_dir)

        extracted = os.listdir(temp_dir)
        py_files = [f for f in extracted if f.endswith('.py')]
        js_files = [f for f in extracted if f.endswith('.js')]

        main_script = None
        file_type = None
        for p in ['main.py', 'bot.py', 'app.py']:
            if p in py_files: main_script = p; file_type = 'py'; break
        if not main_script:
            for p in ['index.js', 'main.js', 'bot.js', 'app.js']:
                if p in js_files: main_script = p; file_type = 'js'; break
        if not main_script:
            if py_files: main_script = py_files[0]; file_type = 'py'
            elif js_files: main_script = js_files[0]; file_type = 'js'
        if not main_script:
            bot.reply_to(message, "❌ Arşivde `.py` veya `.js` bulunamadı!")
            return

        # Dosyaları kullanıcı klasörüne taşı
        for item in os.listdir(temp_dir):
            src = os.path.join(temp_dir, item)
            dst = os.path.join(user_folder, item)
            if os.path.isdir(dst): shutil.rmtree(dst)
            elif os.path.exists(dst): os.remove(dst)
            shutil.move(src, dst)

        save_user_file(user_id, main_script, file_type)
        main_path = os.path.join(user_folder, main_script)
        send_for_approval(user_id, main_script, file_type, main_path, message)
    except zipfile.BadZipFile as e:
        bot.reply_to(message, f"❌ Geçersiz ZIP: {e}")
    except Exception as e:
        logger.error(f"ZIP işleme hatası: {e}", exc_info=True)
        bot.reply_to(message, f"❌ ZIP hatası: {e}")
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try: shutil.rmtree(temp_dir)
            except: pass

# ============================================================
# === MESAJ HANDLER'LARI ===
# ============================================================
def _logic_send_welcome(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    if bot_locked and user_id not in admin_ids:
        bot.send_message(chat_id, "🔒 Bot kilitli. Daha sonra deneyin.")
        return
    # Zorunlu kanal kontrolü
    is_member, missing = check_required_channels(user_id)
    if not is_member:
        bot.send_message(chat_id,
                         "⚠️ **Botu kullanmak için önce kanallara katılmalısınız!**\n\nKatıldıktan sonra ✅ Üyeyim butonuna basın.",
                         reply_markup=get_join_markup(missing), parse_mode='Markdown')
        return

    if user_id not in active_users:
        add_active_user(user_id)
        try:
            bot.send_message(OWNER_ID, f"🆕 Yeni kullanıcı: {message.from_user.first_name} (@{message.from_user.username or 'N/A'}) - `{user_id}`", parse_mode='Markdown')
        except: pass

    file_limit = get_user_file_limit(user_id)
    current = get_user_file_count(user_id)
    limit_str = str(file_limit) if file_limit != float('inf') else "Sınırsız"
    if user_id == OWNER_ID: status = "👑 Sahip"
    elif user_id in admin_ids: status = "🛡️ Admin"
    elif user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now(): status = "⭐ Premium"
    else: status = "👤 Ücretsiz"

    text = (
        f"👋 Merhaba, {message.from_user.first_name}!\n\n"
        f"🆔 ID: `{user_id}`\n"
        f"📊 Durum: {status}\n"
        f"📁 Dosyalar: {current} / {limit_str}\n\n"
        f"🚀 Python (`.py`) veya JS (`.js`) scriptlerini barındır.\n"
        f"📦 Tek dosya veya `.zip` arşiv yükle.\n"
        f"⚠️ Dosyalar **admin onayı** olmadan çalışmaz!"
    )
    bot.send_message(chat_id, text, reply_markup=create_reply_keyboard_main_menu(user_id), parse_mode='Markdown')

def _logic_channels(message):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for ch, url in zip(REQUIRED_CHANNELS, REQUIRED_CHANNEL_URLS):
        markup.add(types.InlineKeyboardButton(f"📢 {ch}", url=url))
    markup.add(types.InlineKeyboardButton(f"📢 Güncelleme Kanalı", url=UPDATE_CHANNEL))
    bot.reply_to(message, "📢 **Kanallarımız:**", reply_markup=markup, parse_mode='Markdown')

def _logic_upload_file(message):
    user_id = message.from_user.id
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "🔒 Bot kilitli.")
        return
    limit = get_user_file_limit(user_id)
    if get_user_file_count(user_id) >= limit:
        bot.reply_to(message, f"❌ Dosya limiti doldu ({get_user_file_count(user_id)}/{limit}). Önce silin.")
        return
    bot.reply_to(message, "📤 Python (`.py`), JS (`.js`) veya ZIP (`.zip`) dosyanı gönder.\n\n⚠️ Onay için admin'e iletilecek.")

def _logic_check_files(message):
    user_id = message.from_user.id
    files = user_files.get(user_id, [])
    if not files:
        bot.reply_to(message, "📂 Henüz dosya yüklemediniz.")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for file_name, file_type in sorted(files):
        running = is_bot_running(user_id, file_name)
        status = "🟢 Çalışıyor" if running else "⚪ Durdu"
        markup.add(types.InlineKeyboardButton(f"{file_name} ({file_type}) - {status}",
                                              callback_data=f'file_{user_id}_{file_name}'))
    bot.reply_to(message, "📂 **Dosyalarınız:**", reply_markup=markup, parse_mode='Markdown')

def _logic_bot_speed(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    start = time.time()
    wait_msg = bot.reply_to(message, "⏱️ Hız testi...")
    try:
        bot.send_chat_action(chat_id, 'typing')
        response_time = round((time.time() - start) * 1000, 2)
        status = "🔓 Açık" if not bot_locked else "🔒 Kilitli"
        speed_msg = f"⚡ **Bot Hızı:** {response_time} ms\n🔐 **Durum:** {status}"
        bot.edit_message_text(speed_msg, chat_id, wait_msg.message_id, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Hız testi hatası: {e}")

def _logic_contact_owner(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📞 İletişim', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'))
    bot.reply_to(message, "📞 **Sahip ile iletişim:**", reply_markup=markup, parse_mode='Markdown')

def _logic_uptime(message):
    bot.reply_to(message, f"⏱️ **Çalışma Süresi:** `{get_uptime()}`", parse_mode='Markdown')

def _logic_statistics(message):
    user_id = message.from_user.id
    running = sum(1 for k in bot_scripts if is_bot_running(*k.split('_', 1)))
    text = (
        f"📊 **İstatistikler**\n\n"
        f"👥 Toplam Kullanıcı: {len(active_users)}\n"
        f"📁 Toplam Dosya: {sum(len(f) for f in user_files.values())}\n"
        f"🤖 Çalışan Bot: {running}\n"
        f"⏳ Bekleyen Onay: {len(pending_approvals)}"
    )
    bot.reply_to(message, text, parse_mode='Markdown')

def _logic_sys_stats(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "❌ Admin gerekli.")
        return
    s = get_system_stats()
    if not s:
        bot.reply_to(message, "❌ Sistem bilgisi alınamadı.")
        return
    text = (
        f"💻 **Sistem Durumu**\n\n"
        f"🔧 **CPU:** %{s['cpu_percent']}\n"
        f"🧠 **RAM:** %{s['ram_percent']} ({s['ram_used_mb']}/{s['ram_total_mb']} MB)\n"
        f"💾 **Disk:** %{s['disk_percent']} ({s['disk_used_gb']}/{s['disk_total_gb']} GB)\n"
        f"⏱️ **Uptime:** {s['uptime']}\n"
        f"🤖 **Çalışan Bot:** {s['running_bots']}\n"
        f"⏳ **Bekleyen Onay:** {s['pending_approvals']}"
    )
    bot.reply_to(message, text, parse_mode='Markdown')

def _logic_broadcast_init(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "❌ Admin gerekli.")
        return
    msg = bot.reply_to(message, "📢 Yayınlanacak mesajı gönder.\n/iptal ile iptal.")
    bot.register_next_step_handler(msg, process_broadcast_message)

def _logic_toggle_lock_bot(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "❌ Admin gerekli.")
        return
    global bot_locked
    bot_locked = not bot_locked
    status = "kilitlendi" if bot_locked else "kilidi açıldı"
    bot.reply_to(message, f"{'🔒' if bot_locked else '🔓'} Bot {status}.")

def _logic_admin_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "❌ Admin gerekli.")
        return
    bot.reply_to(message, "👑 **Admin Panel**", reply_markup=create_admin_panel(), parse_mode='Markdown')

def _logic_subscriptions_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "❌ Admin gerekli.")
        return
    bot.reply_to(message, "💳 **Abonelik Yönetimi**", reply_markup=create_subscription_menu(), parse_mode='Markdown')

def _logic_run_all_scripts(message_or_call):
    if isinstance(message_or_call, types.Message):
        admin_user_id = message_or_call.from_user.id
        chat_id = message_or_call.chat.id
        reply = lambda t, **k: bot.reply_to(message_or_call, t, **k)
    else:
        admin_user_id = message_or_call.from_user.id
        chat_id = message_or_call.message.chat.id
        bot.answer_callback_query(message_or_call.id)
        reply = lambda t, **k: bot.send_message(chat_id, t, **k)
    if admin_user_id not in admin_ids:
        reply("❌ Admin gerekli."); return
    reply("🟢 Tüm kullanıcı scriptleri başlatılıyor...")
    started = 0
    for target_user_id, files in dict(user_files).items():
        for file_name, file_type in files:
            if not is_bot_running(target_user_id, file_name):
                path = os.path.join(get_user_folder(target_user_id), file_name)
                if os.path.exists(path):
                    if file_type == 'py':
                        threading.Thread(target=run_script, args=(path, target_user_id, get_user_folder(target_user_id), file_name, message_or_call.message if hasattr(message_or_call, 'message') else message_or_call)).start()
                    elif file_type == 'js':
                        threading.Thread(target=run_js_script, args=(path, target_user_id, get_user_folder(target_user_id), file_name, message_or_call.message if hasattr(message_or_call, 'message') else message_or_call)).start()
                    started += 1
                    time.sleep(0.3)
    reply(f"✅ {started} script başlatıldı.")

def _logic_venv_bots(message):
    """Sanal ortamdaki botları listele + indir."""
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "❌ Admin gerekli.")
        return
    venvs = []
    if os.path.exists(VENV_DIR):
        for uid in os.listdir(VENV_DIR):
            venv_path = os.path.join(VENV_DIR, uid)
            if os.path.isdir(venv_path):
                venvs.append(uid)
    if not venvs:
        bot.reply_to(message, "🐍 Sanal ortam botu yok.")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for uid in venvs:
        markup.add(types.InlineKeyboardButton(f"🐍 Kullanıcı {uid}", callback_data=f'venv_dl_{uid}'))
    bot.reply_to(message, "🐍 **Sanal Ortam Botları:**", reply_markup=markup, parse_mode='Markdown')

# ============================================================
# === MPX AI ===
# ============================================================
def handle_mpx_command(message):
    user_id = message.from_user.id
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "🔒 Bot kilitli.")
        return
    if not message.text or len(message.text.split()) < 2:
        bot.reply_to(message, "🤖 `/mpx <soru>` şeklinde kullanın.\nÖrnek: `/mpx Yapay zeka nedir?`", parse_mode='Markdown')
        return
    query = message.text.split(' ', 1)[1]
    bot.send_chat_action(message.chat.id, 'typing')
    try:
        headers = {"Authorization": f"Bearer {A4F_API_KEY}", "Content-Type": "application/json"}
        payload = {"model": A4F_MODEL, "messages": [{"role": "user", "content": query}], "temperature": 0.7}
        response = requests.post(A4F_API_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        answer = result.get('choices', [{}])[0].get('message', {}).get('content', 'Yanıt yok')
        if len(answer) > 4000:
            for x in range(0, len(answer), 4000):
                bot.reply_to(message, answer[x:x+4000])
        else:
            bot.reply_to(message, answer)
    except Exception as e:
        logger.error(f"API hatası: {e}")
        bot.reply_to(message, "❌ API hatası. Sonra deneyin.")

# ============================================================
# === BOT KOMUTLARI ===
# ============================================================
@bot.message_handler(commands=['start', 'help'])
def cmd_start(message): _logic_send_welcome(message)

@bot.message_handler(commands=['ping'])
def cmd_ping(message):
    start = time.time()
    msg = bot.reply_to(message, "Pong!")
    latency = round((time.time() - start) * 1000, 2)
    bot.edit_message_text(f"🏓 Pong!\n⏱️ Gecikme: {latency} ms\n⏱️ Uptime: {get_uptime()}",
                          message.chat.id, msg.message_id)

@bot.message_handler(commands=['mpx'])
def cmd_mpx(message): handle_mpx_command(message)

@bot.message_handler(commands=['uptime'])
def cmd_uptime(message): _logic_uptime(message)

@bot.message_handler(commands=['stats'])
def cmd_stats(message): _logic_statistics(message)

@bot.message_handler(commands=['sysinfo'])
def cmd_sysinfo(message): _logic_sys_stats(message)

# Buton metinleri → fonksiyonlar
BUTTON_LOGIC = {
    BTN_STYLE["kanal"]: _logic_channels,
    BTN_STYLE["yukle"]: _logic_upload_file,
    BTN_STYLE["kontrol"]: _logic_check_files,
    BTN_STYLE["hiz"]: _logic_bot_speed,
    BTN_STYLE["istat"]: _logic_statistics,
    BTN_STYLE["iletisim"]: _logic_contact_owner,
    BTN_STYLE["uptime"]: _logic_uptime,
    BTN_STYLE["ai"]: lambda m: bot.reply_to(m, "🤖 `/mpx <soru>` kullanın.", parse_mode='Markdown'),
    BTN_STYLE["abonelik"]: _logic_subscriptions_panel,
    BTN_STYLE["yayin"]: _logic_broadcast_init,
    BTN_STYLE["kilit"]: _logic_toggle_lock_bot,
    BTN_STYLE["kilit_ac"]: _logic_toggle_lock_bot,
    BTN_STYLE["admin"]: _logic_admin_panel,
    BTN_STYLE["tumu"]: _logic_run_all_scripts,
    BTN_STYLE["sanal"]: _logic_venv_bots,
    BTN_STYLE["cpu"]: _logic_sys_stats,
}

@bot.message_handler(func=lambda m: m.text in BUTTON_LOGIC)
def handle_button_text(message):
    fn = BUTTON_LOGIC.get(message.text)
    if fn: fn(message)

# ============================================================
# === DOSYA YÜKLEME ===
# ============================================================
@bot.message_handler(content_types=['document'])
def handle_file_upload_doc(message):
    user_id = message.from_user.id
    doc = message.document
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "🔒 Bot kilitli."); return
    if get_user_file_count(user_id) >= get_user_file_limit(user_id):
        bot.reply_to(message, "❌ Dosya limiti doldu."); return
    file_name = doc.file_name
    if not file_name:
        bot.reply_to(message, "❌ Dosya adı yok."); return
    ext = os.path.splitext(file_name)[1].lower()
    if ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, "❌ Sadece `.py`, `.js`, `.zip`!"); return
    if doc.file_size > 20 * 1024 * 1024:
        bot.reply_to(message, "❌ Dosya 20 MB'dan büyük!"); return
    try:
        wait = bot.reply_to(message, f"📥 `{file_name}` indiriliyor...", parse_mode='Markdown')
        file_info = bot.get_file(doc.file_id)
        content = bot.download_file(file_info.file_path)
        bot.edit_message_text(f"✅ İndirildi. İşleniyor...", message.chat.id, wait.message_id)
        user_folder = get_user_folder(user_id)
        if ext == '.zip':
            handle_zip_file(content, file_name, message)
        else:
            path = os.path.join(user_folder, file_name)
            with open(path, 'wb') as f: f.write(content)
            save_user_file(user_id, file_name, 'py' if ext == '.py' else 'js')
            send_for_approval(user_id, file_name, 'py' if ext == '.py' else 'js', path, message)
    except Exception as e:
        logger.error(f"Dosya işleme hatası: {e}")
        bot.reply_to(message, f"❌ Hata: {e}")

# ============================================================
# === CALLBACK HANDLER ===
# ============================================================
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    data = call.data
    try:
        if data == 'check_join':
            ok, missing = check_required_channels(user_id)
            if ok:
                bot.answer_callback_query(call.id, "✅ Onaylandı!")
                bot.send_message(call.message.chat.id, "✅ Teşekkürler! /start ile devam edin.")
            else:
                bot.answer_callback_query(call.id, f"❌ Hâlâ eksik: {', '.join(missing)}", show_alert=True)
            return
        if data == 'channels': _logic_channels(call.message); bot.answer_callback_query(call.id); return
        if data == 'upload': _logic_upload_file(call.message); bot.answer_callback_query(call.id); return
        if data == 'check_files': _logic_check_files(call.message); bot.answer_callback_query(call.id); return
        if data == 'speed': _logic_bot_speed(call.message); bot.answer_callback_query(call.id); return
        if data == 'stats': _logic_statistics(call.message); bot.answer_callback_query(call.id); return
        if data == 'uptime': _logic_uptime(call.message); bot.answer_callback_query(call.id); return
        if data == 'sys_stats': _logic_sys_stats(call.message); bot.answer_callback_query(call.id); return
        if data == 'back_to_main': _logic_send_welcome(call.message); bot.answer_callback_query(call.id); return
        if data == 'mpx_ai':
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id, "🤖 `/mpx <soru>` kullanın.", parse_mode='Markdown'); return
        if data == 'subscription': _logic_subscriptions_panel(call.message); bot.answer_callback_query(call.id); return
        if data == 'admin_panel': _logic_admin_panel(call.message); bot.answer_callback_query(call.id); return
        if data == 'broadcast': _logic_broadcast_init(call.message); bot.answer_callback_query(call.id); return
        if data == 'lock_bot':
            if user_id in admin_ids:
                global bot_locked; bot_locked = True
                bot.answer_callback_query(call.id, "🔒 Kilitlendi."); return
        if data == 'unlock_bot':
            if user_id in admin_ids:
                bot_locked = False
                bot.answer_callback_query(call.id, "🔓 Açıldı."); return
        if data == 'run_all_scripts': _logic_run_all_scripts(call); return
        if data == 'venv_bots': _logic_venv_bots(call.message); bot.answer_callback_query(call.id); return
        # Onay/Red
        if data.startswith('approve_'):
            handle_approve(call, data.split('_', 1)[1]); return
        if data.startswith('reject_'):
            handle_reject(call, data.split('_', 1)[1]); return
        # Venv indirme
        if data.startswith('venv_dl_'):
            if user_id not in admin_ids: bot.answer_callback_query(call.id, "❌ Admin gerekli.", show_alert=True); return
            uid = data.split('_')[2]
            venv_path = os.path.join(VENV_DIR, uid)
            if not os.path.isdir(venv_path): bot.answer_callback_query(call.id, "❌ Yok.", show_alert=True); return
            bot.answer_callback_query(call.id, "📦 ZIP hazırlanıyor...")
            try:
                zip_path = os.path.join(tempfile.gettempdir(), f"venv_{uid}_{int(time.time())}.zip")
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for root, _, files in os.walk(venv_path):
                        for f in files:
                            fp = os.path.join(root, f)
                            arc = os.path.relpath(fp, venv_path)
                            try: zf.write(fp, arc)
                            except: pass
                with open(zip_path, 'rb') as zf:
                    bot.send_document(call.message.chat.id, zf, caption=f"🐍 Venv: {uid}")
                os.remove(zip_path)
            except Exception as e:
                bot.send_message(call.message.chat.id, f"❌ Hata: {e}")
            return
        # Dosya kontrol
        if data.startswith('file_'):
            _, owner_str, fname = data.split('_', 2)
            owner = int(owner_str)
            if not (user_id == owner or user_id in admin_ids):
                bot.answer_callback_query(call.id, "❌ Yetkiniz yok.", show_alert=True); return
            running = is_bot_running(owner, fname)
            status = "🟢 Çalışıyor" if running else "⚪ Durdu"
            bot.edit_message_text(f"📄 `{fname}`\n📊 {status}\n👤 Kullanıcı: `{owner}`",
                                  call.message.chat.id, call.message.message_id,
                                  reply_markup=create_control_buttons(owner, fname, running), parse_mode='Markdown')
            bot.answer_callback_query(call.id); return
        # Start/Stop/Restart/Delete/Logs
        if data.startswith(('start_', 'stop_', 'restart_', 'delete_', 'logs_')):
            handle_file_action(call, data); return
        # Admin işlemleri
        if data in ['add_admin', 'remove_admin', 'list_admins']:
            handle_admin_action(call, data); return
        if data in ['add_subscription', 'remove_subscription', 'check_subscription']:
            handle_sub_action(call, data); return
        bot.answer_callback_query(call.id, "❓ Bilinmeyen işlem.")
    except Exception as e:
        logger.error(f"Callback hatası '{data}': {e}", exc_info=True)
        try: bot.answer_callback_query(call.id, "❌ Hata.", show_alert=True)
        except: pass

def handle_approve(call, file_key):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "❌ Sadece sahip onaylayabilir.", show_alert=True); return
    if file_key not in pending_approvals:
        bot.answer_callback_query(call.id, "❌ Onay bulunamadı.", show_alert=True); return
    p = pending_approvals[file_key]
    bot.answer_callback_query(call.id, "✅ Onaylandı, başlatılıyor...")
    try:
        bot.edit_message_text(f"✅ ONAYLANDI: `{p['file_name']}`", call.message.chat.id, call.message.message_id)
    except: pass
    # Kullanıcıya bilgi
    try:
        bot.send_message(p['chat_id'], f"✅ Dosyanız onaylandı: `{p['file_name']}`\n🚀 Başlatılıyor...", parse_mode='Markdown')
    except: pass
    # Script başlat
    class FakeMsg:
        def __init__(self, chat_id): self.chat = type('C', (), {'id': chat_id})()
    fake = FakeMsg(p['chat_id'])
    user_folder = get_user_folder(p['user_id'])
    if p['file_type'] == 'py':
        threading.Thread(target=run_script, args=(p['file_path'], p['user_id'], user_folder, p['file_name'], fake)).start()
    elif p['file_type'] == 'js':
        threading.Thread(target=run_js_script, args=(p['file_path'], p['user_id'], user_folder, p['file_name'], fake)).start()
    del pending_approvals[file_key]

def handle_reject(call, file_key):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "❌ Sadece sahip reddedebilir.", show_alert=True); return
    if file_key not in pending_approvals:
        bot.answer_callback_query(call.id, "❌ Onay bulunamadı.", show_alert=True); return
    p = pending_approvals[file_key]
    bot.answer_callback_query(call.id, "❌ Reddedildi.")
    try:
        bot.edit_message_text(f"❌ REDDEDİLDİ: `{p['file_name']}`", call.message.chat.id, call.message.message_id)
    except: pass
    # Dosyayı sil
    try:
        if os.path.exists(p['file_path']): os.remove(p['file_path'])
        remove_user_file_db(p['user_id'], p['file_name'])
    except: pass
    try:
        bot.send_message(p['chat_id'], f"❌ Dosyanız reddedildi: `{p['file_name']}`\n\nAdmin uygun görmedi.", parse_mode='Markdown')
    except: pass
    del pending_approvals[file_key]

def handle_file_action(call, data):
    user_id = call.from_user.id
    parts = data.split('_', 2)
    action, owner_str, fname = parts[0], parts[1], parts[2]
    owner = int(owner_str)
    if not (user_id == owner or user_id in admin_ids):
        bot.answer_callback_query(call.id, "❌ Yetkiniz yok.", show_alert=True); return
    key = f"{owner}_{fname}"
    if action == 'start':
        if is_bot_running(owner, fname):
            bot.answer_callback_query(call.id, "⚠️ Zaten çalışıyor.", show_alert=True); return
        bot.answer_callback_query(call.id, "🚀 Başlatılıyor...")
        user_folder = get_user_folder(owner)
        path = os.path.join(user_folder, fname)
        ftype = next((ft for fn, ft in user_files.get(owner, []) if fn == fname), 'py')
        if ftype == 'py':
            threading.Thread(target=run_script, args=(path, owner, user_folder, fname, call.message)).start()
        else:
            threading.Thread(target=run_js_script, args=(path, owner, user_folder, fname, call.message)).start()
    elif action == 'stop':
        info = bot_scripts.get(key)
        if info: kill_process_tree(info)
        if key in bot_scripts: del bot_scripts[key]
        bot.answer_callback_query(call.id, "🛑 Durduruldu.")
        try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_control_buttons(owner, fname, False))
        except: pass
    elif action == 'restart':
        info = bot_scripts.get(key)
        if info: kill_process_tree(info)
        if key in bot_scripts: del bot_scripts[key]
        time.sleep(0.3)
        user_folder = get_user_folder(owner)
        path = os.path.join(user_folder, fname)
        ftype = next((ft for fn, ft in user_files.get(owner, []) if fn == fname), 'py')
        if ftype == 'py':
            threading.Thread(target=run_script, args=(path, owner, user_folder, fname, call.message)).start()
        else:
            threading.Thread(target=run_js_script, args=(path, owner, user_folder, fname, call.message)).start()
        bot.answer_callback_query(call.id, "🔄 Yeniden başlatıldı.")
    elif action == 'delete':
        info = bot_scripts.get(key)
        if info: kill_process_tree(info)
        if key in bot_scripts: del bot_scripts[key]
        user_folder = get_user_folder(owner)
        for ext in ['.log']:
            lp = os.path.join(user_folder, os.path.splitext(fname)[0] + ext)
            if os.path.exists(lp): 
                try: os.remove(lp)
                except: pass
        fp = os.path.join(user_folder, fname)
        if os.path.exists(fp):
            try: os.remove(fp)
            except: pass
        remove_user_file_db(owner, fname)
        bot.answer_callback_query(call.id, "🗑️ Silindi.")
        try: bot.edit_message_text(f"🗑️ `{fname}` silindi.", call.message.chat.id, call.message.message_id)
        except: pass
    elif action == 'logs':
        lp = os.path.join(get_user_folder(owner), os.path.splitext(fname)[0] + '.log')
        if not os.path.exists(lp):
            bot.answer_callback_query(call.id, "❌ Log yok.", show_alert=True); return
        try:
            with open(lp, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()[-3500:]
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id, f"📜 `{fname}` logları:\n```\n{content}\n```", parse_mode='Markdown')
        except Exception as e:
            bot.answer_callback_query(call.id, f"❌ {e}", show_alert=True)

def handle_admin_action(call, action):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin gerekli.", show_alert=True); return
    if action == 'list_admins':
        txt = "\n".join(f"- `{a}` {'(Sahip)' if a == OWNER_ID else ''}" for a in sorted(admin_ids))
        bot.edit_message_text(f"👑 Adminler:\n{txt}", call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel(), parse_mode='Markdown')
        bot.answer_callback_query(call.id); return
    if action == 'add_admin':
        if call.from_user.id != OWNER_ID:
            bot.answer_callback_query(call.id, "❌ Sadece sahip.", show_alert=True); return
        msg = bot.send_message(call.message.chat.id, "🆔 Yeni admin ID'sini girin.\n/iptal")
        bot.register_next_step_handler(msg, process_add_admin); bot.answer_callback_query(call.id); return
    if action == 'remove_admin':
        if call.from_user.id != OWNER_ID:
            bot.answer_callback_query(call.id, "❌ Sadece sahip.", show_alert=True); return
        msg = bot.send_message(call.message.chat.id, "🆔 Çıkarılacak admin ID'sini girin.\n/iptal")
        bot.register_next_step_handler(msg, process_remove_admin); bot.answer_callback_query(call.id); return

def process_add_admin(message):
    if message.from_user.id != OWNER_ID: return
    if message.text.strip() == '/iptal': bot.reply_to(message, "İptal."); return
    try:
        aid = int(message.text.strip())
        add_admin_db(aid)
        bot.reply_to(message, f"✅ Admin `{aid}` eklendi.")
        try: bot.send_message(aid, "🎉 Admin oldunuz!")
        except: pass
    except:
        bot.reply_to(message, "❌ Geçersiz ID.")

def process_remove_admin(message):
    if message.from_user.id != OWNER_ID: return
    if message.text.strip() == '/iptal': bot.reply_to(message, "İptal."); return
    try:
        aid = int(message.text.strip())
        if aid == OWNER_ID: bot.reply_to(message, "❌ Sahip çıkarılamaz."); return
        if remove_admin_db(aid): bot.reply_to(message, f"✅ Admin `{aid}` çıkarıldı.")
        else: bot.reply_to(message, "❌ Başarısız.")
    except:
        bot.reply_to(message, "❌ Geçersiz ID.")

def handle_sub_action(call, action):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin gerekli.", show_alert=True); return
    if action == 'add_subscription':
        msg = bot.send_message(call.message.chat.id, "🆔 `ID gün` formatında girin.\nÖrnek: `12345678 30`\n/iptal")
        bot.register_next_step_handler(msg, process_add_sub); bot.answer_callback_query(call.id); return
    if action == 'remove_subscription':
        msg = bot.send_message(call.message.chat.id, "🆔 Abonelik silinecek kullanıcı ID'si.\n/iptal")
        bot.register_next_step_handler(msg, process_remove_sub); bot.answer_callback_query(call.id); return
    if action == 'check_subscription':
        msg = bot.send_message(call.message.chat.id, "🆔 Kontrol edilecek kullanıcı ID'si.\n/iptal")
        bot.register_next_step_handler(msg, process_check_sub); bot.answer_callback_query(call.id); return

def process_add_sub(message):
    if message.from_user.id not in admin_ids: return
    if message.text.strip() == '/iptal': bot.reply_to(message, "İptal."); return
    try:
        parts = message.text.split()
        uid = int(parts[0]); days = int(parts[1])
        current = user_subscriptions.get(uid, {}).get('expiry')
        start = current if current and current > datetime.now() else datetime.now()
        new_expiry = start + timedelta(days=days)
        save_subscription(uid, new_expiry)
        bot.reply_to(message, f"✅ `{uid}` için {days} gün abonelik eklendi. Bitiş: {new_expiry.strftime('%d.%m.%Y')}")
        try: bot.send_message(uid, f"⭐ Abonelik aktif! Bitiş: {new_expiry.strftime('%d.%m.%Y')}")
        except: pass
    except Exception as e:
        bot.reply_to(message, f"❌ Hata: {e}. Format: `ID gün`")

def process_remove_sub(message):
    if message.from_user.id not in admin_ids: return
    if message.text.strip() == '/iptal': bot.reply_to(message, "İptal."); return
    try:
        uid = int(message.text.strip())
        remove_subscription_db(uid)
        bot.reply_to(message, f"✅ `{uid}` aboneliği silindi.")
    except:
        bot.reply_to(message, "❌ Geçersiz ID.")

def process_check_sub(message):
    if message.from_user.id not in admin_ids: return
    if message.text.strip() == '/iptal': bot.reply_to(message, "İptal."); return
    try:
        uid = int(message.text.strip())
        if uid in user_subscriptions:
            e = user_subscriptions[uid]['expiry']
            if e > datetime.now():
                left = (e - datetime.now()).days
                bot.reply_to(message, f"✅ `{uid}` aktif. Bitiş: {e.strftime('%d.%m.%Y')} ({left} gün kaldı)")
            else:
                bot.reply_to(message, f"⚠️ `{uid}` süresi dolmuş ({e.strftime('%d.%m.%Y')})")
        else:
            bot.reply_to(message, f"❌ `{uid}` aboneliği yok.")
    except:
        bot.reply_to(message, "❌ Geçersiz ID.")

def process_broadcast_message(message):
    if message.from_user.id not in admin_ids: return
    if message.text and message.text.lower() == '/iptal': bot.reply_to(message, "İptal."); return
    text = message.text or "(medya)"
    count = 0
    for uid in list(active_users):
        try:
            if message.text: bot.send_message(uid, message.text)
            elif message.photo: bot.send_photo(uid, message.photo[-1].file_id, caption=message.caption or "")
            elif message.video: bot.send_video(uid, message.video.file_id, caption=message.caption or "")
            count += 1
            time.sleep(0.05)
        except: pass
    bot.reply_to(message, f"✅ Yayın tamamlandı: {count} kullanıcı.")

# ============================================================
# === WEB ADMIN PANEL ===
# ============================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Rinex Bot - Admin Panel</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: linear-gradient(135deg, #0f0c29, #302b63, #24243e); color: #fff;
       min-height: 100vh; padding: 20px; }
.header { text-align: center; margin-bottom: 30px; }
.header h1 { font-size: 2.5em; background: linear-gradient(90deg, #00d4ff, #7b2ff7); 
             -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
.stat-card { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
             border-radius: 16px; padding: 20px; backdrop-filter: blur(10px); transition: transform 0.3s; }
.stat-card:hover { transform: translateY(-5px); }
.stat-card h3 { color: #888; font-size: 0.9em; margin-bottom: 10px; text-transform: uppercase; }
.stat-card .value { font-size: 2.2em; font-weight: bold; background: linear-gradient(90deg, #00d4ff, #7b2ff7);
                    -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.progress-bar { width: 100%; height: 8px; background: rgba(255,255,255,0.1); border-radius: 4px; margin-top: 10px; overflow: hidden; }
.progress-fill { height: 100%; background: linear-gradient(90deg, #00d4ff, #7b2ff7); transition: width 0.5s; }
.section { background: rgba(255,255,255,0.05); border-radius: 16px; padding: 20px; margin-bottom: 20px; }
.section h2 { margin-bottom: 15px; color: #00d4ff; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 10px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.1); }
th { color: #888; font-size: 0.85em; text-transform: uppercase; }
.btn { padding: 8px 16px; border-radius: 8px; border: none; cursor: pointer; font-weight: 600; transition: all 0.3s; }
.btn-danger { background: #ff4757; color: white; }
.btn-success { background: #2ed573; color: white; }
.btn:hover { transform: scale(1.05); }
.refresh { position: fixed; top: 20px; right: 20px; background: #00d4ff; color: #000; padding: 10px 20px; 
           border-radius: 8px; border: none; cursor: pointer; font-weight: bold; }
.login-box { max-width: 400px; margin: 100px auto; background: rgba(255,255,255,0.05); padding: 40px;
             border-radius: 16px; border: 1px solid rgba(255,255,255,0.1); }
.login-box input { width: 100%; padding: 12px; margin-bottom: 15px; background: rgba(0,0,0,0.3);
                   border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; color: #fff; }
.login-box button { width: 100%; padding: 12px; background: linear-gradient(90deg, #00d4ff, #7b2ff7);
                    border: none; border-radius: 8px; color: #fff; font-weight: bold; cursor: pointer; }
.live-dot { display: inline-block; width: 10px; height: 10px; background: #2ed573; border-radius: 50%; 
            margin-right: 8px; animation: pulse 1.5s infinite; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
</style>
</head>
<body>
{% if not logged_in %}
<div class="login-box">
  <h2 style="text-align:center;margin-bottom:20px;">🔐 Admin Girişi</h2>
  <form method="POST" action="/login">
    <input type="text" name="username" placeholder="Kullanıcı Adı" required>
    <input type="password" name="password" placeholder="Şifre" required>
    <button type="submit">Giriş Yap</button>
  </form>
  {% if error %}<p style="color:#ff4757;text-align:center;margin-top:15px;">{{ error }}</p>{% endif %}
</div>
{% else %}
<button class="refresh" onclick="location.reload()">🔄 Yenile</button>
<div class="header">
  <h1>🤖 Rinex Bot Yönetim Paneli</h1>
  <p><span class="live-dot"></span>Canlı İzleme</p>
</div>

<div class="stats-grid">
  <div class="stat-card">
    <h3>CPU Kullanımı</h3>
    <div class="value" id="cpu">{{ stats.cpu_percent }}%</div>
    <div class="progress-bar"><div class="progress-fill" id="cpu-bar" style="width:{{ stats.cpu_percent }}%"></div></div>
  </div>
  <div class="stat-card">
    <h3>RAM Kullanımı</h3>
    <div class="value" id="ram">{{ stats.ram_percent }}%</div>
    <div class="progress-bar"><div class="progress-fill" id="ram-bar" style="width:{{ stats.ram_percent }}%"></div></div>
    <small>{{ stats.ram_used_mb }} / {{ stats.ram_total_mb }} MB</small>
  </div>
  <div class="stat-card">
    <h3>Disk</h3>
    <div class="value">{{ stats.disk_percent }}%</div>
    <div class="progress-bar"><div class="progress-fill" style="width:{{ stats.disk_percent }}%"></div></div>
    <small>{{ stats.disk_used_gb }} / {{ stats.disk_total_gb }} GB</small>
  </div>
  <div class="stat-card">
    <h3>Çalışan Bot</h3>
    <div class="value">{{ stats.running_bots }}</div>
  </div>
  <div class="stat-card">
    <h3>Bekleyen Onay</h3>
    <div class="value">{{ stats.pending_approvals }}</div>
  </div>
  <div class="stat-card">
    <h3>Uptime</h3>
    <div class="value" style="font-size:1.2em;">{{ stats.uptime }}</div>
  </div>
</div>

<div class="section">
  <h2>🚀 Çalışan Botlar</h2>
  <table>
    <thead><tr><th>Dosya</th><th>Kullanıcı</th><th>Tür</th><th>PID</th><th>Başlangıç</th><th>İşlem</th></tr></thead>
    <tbody>
    {% for s in scripts %}
    <tr>
      <td>{{ s.file_name }}</td>
      <td>{{ s.script_owner_id }}</td>
      <td>{{ s.type }}</td>
      <td>{{ s.pid }}</td>
      <td>{{ s.start_time }}</td>
      <td><a href="/stop/{{ s.key }}" class="btn btn-danger">Durdur</a></td>
    </tr>
    {% else %}
    <tr><td colspan="6" style="text-align:center;color:#888;">Çalışan bot yok</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>

<div class="section">
  <h2>⏳ Bekleyen Onaylar</h2>
  <table>
    <thead><tr><th>Dosya</th><th>Kullanıcı</th><th>Tür</th><th>İşlem</th></tr></thead>
    <tbody>
    {% for k, p in approvals.items() %}
    <tr>
      <td>{{ p.file_name }}</td>
      <td>{{ p.user_id }}</td>
      <td>{{ p.file_type }}</td>
      <td>
        <a href="/approve/{{ k }}" class="btn btn-success">✅ Onayla</a>
        <a href="/reject/{{ k }}" class="btn btn-danger">❌ Reddet</a>
      </td>
    </tr>
    {% else %}
    <tr><td colspan="4" style="text-align:center;color:#888;">Bekleyen onay yok</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>

<script>
setInterval(() => {
  fetch('/api/stats').then(r => r.json()).then(d => {
    document.getElementById('cpu').textContent = d.cpu_percent + '%';
    document.getElementById('cpu-bar').style.width = d.cpu_percent + '%';
    document.getElementById('ram').textContent = d.ram_percent + '%';
    document.getElementById('ram-bar').style.width = d.ram_percent + '%';
  });
}, 3000);
</script>
{% endif %}
</body>
</html>
"""

@app.route('/')
def home():
    if not session.get('logged_in'):
        return render_template_string(DASHBOARD_HTML, logged_in=False, error=None)
    scripts_info = []
    for k, v in bot_scripts.items():
        scripts_info.append({
            'key': k, 'file_name': v['file_name'], 'script_owner_id': v['script_owner_id'],
            'type': v['type'], 'pid': v['process'].pid,
            'start_time': v['start_time'].strftime('%H:%M:%S')
        })
    return render_template_string(DASHBOARD_HTML, logged_in=True,
                                  stats=get_system_stats(), scripts=scripts_info,
                                  approvals=pending_approvals)

@app.route('/login', methods=['POST'])
def login():
    if request.form.get('username') == WEB_ADMIN_USER and request.form.get('password') == WEB_ADMIN_PASS:
        session['logged_in'] = True
        return redirect(url_for('home'))
    return render_template_string(DASHBOARD_HTML, logged_in=False, error="Hatalı giriş!")

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/api/stats')
def api_stats():
    if not session.get('logged_in'): return jsonify({})
    return jsonify(get_system_stats())

@app.route('/stop/<key>')
def web_stop(key):
    if not session.get('logged_in'): return redirect(url_for('home'))
    if key in bot_scripts:
        kill_process_tree(bot_scripts[key])
        del bot_scripts[key]
    return redirect(url_for('home'))

@app.route('/approve/<file_key>')
def web_approve(file_key):
    if not session.get('logged_in'): return redirect(url_for('home'))
    if file_key in pending_approvals:
        p = pending_approvals[file_key]
        class FakeMsg:
            def __init__(self, chat_id): self.chat = type('C', (), {'id': chat_id})()
        fake = FakeMsg(p['chat_id'])
        user_folder = get_user_folder(p['user_id'])
        if p['file_type'] == 'py':
            threading.Thread(target=run_script, args=(p['file_path'], p['user_id'], user_folder, p['file_name'], fake)).start()
        else:
            threading.Thread(target=run_js_script, args=(p['file_path'], p['user_id'], user_folder, p['file_name'], fake)).start()
        try: bot.send_message(p['chat_id'], f"✅ Dosyanız onaylandı: `{p['file_name']}`", parse_mode='Markdown')
        except: pass
        del pending_approvals[file_key]
    return redirect(url_for('home'))

@app.route('/reject/<file_key>')
def web_reject(file_key):
    if not session.get('logged_in'): return redirect(url_for('home'))
    if file_key in pending_approvals:
        p = pending_approvals[file_key]
        try:
            if os.path.exists(p['file_path']): os.remove(p['file_path'])
            remove_user_file_db(p['user_id'], p['file_name'])
            bot.send_message(p['chat_id'], f"❌ Dosyanız reddedildi: `{p['file_name']}`", parse_mode='Markdown')
        except: pass
        del pending_approvals[file_key]
    return redirect(url_for('home'))

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, threaded=True, debug=False, use_reloader=False)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    logger.info("🌐 Web panel başladı.")

# ============================================================
# === OTOMATİK TEMİZLİK ===
# ============================================================
def auto_cleanup_thread():
    while True:
        try:
            gc.collect()
            for folder in os.listdir(UPLOAD_BOTS_DIR):
                fp = os.path.join(UPLOAD_BOTS_DIR, folder)
                if os.path.isdir(fp) and not os.listdir(fp):
                    try: os.rmdir(fp)
                    except: pass
            for root, _, files in os.walk(UPLOAD_BOTS_DIR):
                for f in files:
                    if f.endswith('.log'):
                        p = os.path.join(root, f)
                        try:
                            if os.path.getsize(p) > 5 * 1024 * 1024:
                                with open(p, 'r+', encoding='utf-8', errors='ignore') as lf:
                                    lf.seek(-2*1024*1024, os.SEEK_END)
                                    tail = lf.read()
                                with open(p, 'w', encoding='utf-8') as lf:
                                    lf.write("...(kırpıldı)...\n" + tail)
                        except: pass
        except Exception as e:
            logger.error(f"Temizlik hatası: {e}")
        time.sleep(300)

def cleanup():
    logger.warning("Kapatma - süreçler temizleniyor...")
    for key in list(bot_scripts.keys()):
        if key in bot_scripts:
            kill_process_tree(bot_scripts[key])
    logger.warning("Temizlik tamam.")
atexit.register(cleanup)

# ============================================================
# === BAŞLATMA ===
# ============================================================
if __name__ == '__main__':
    logger.info("="*50)
    logger.info("🤖 Rinex Bot v2 Başlatılıyor")
    logger.info(f"Python: {sys.version.split()[0]}")
    logger.info(f"Owner: {OWNER_ID}")
    logger.info(f"Kanallar: {REQUIRED_CHANNELS}")
    logger.info(f"Sanal ortam dizini: {VENV_DIR}")
    logger.info("="*50)
    keep_alive()
    threading.Thread(target=auto_cleanup_thread, daemon=True).start()
    logger.info("🔄 Polling başlıyor...")
    while True:
        try:
            bot.infinity_polling(logger_level=logging.INFO, timeout=60, long_polling_timeout=30)
        except requests.exceptions.ReadTimeout:
            logger.warning("ReadTimeout, 5sn sonra..."); time.sleep(5)
        except requests.exceptions.ConnectionError as ce:
            logger.error(f"Bağlantı hatası: {ce}, 15sn sonra..."); time.sleep(15)
        except Exception as e:
            logger.critical(f"Kritik hata: {e}", exc_info=True)
            logger.info("30sn sonra yeniden..."); time.sleep(30)
