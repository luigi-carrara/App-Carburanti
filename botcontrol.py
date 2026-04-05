import subprocess
import sys
import socket
import psutil
import shutil
import json
import platform
from telebot import TeleBot, types
import requests
import os
from fastapi import FastAPI, HTTPException, Query, Header, Depends
from dotenv import load_dotenv



# Caricamento configurazioni
load_dotenv("config.env")
TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("TELEGRAM_CHAT_ID"))
ENV = os.getenv("ENV", "LOCAL")
API_KEY_EXPECTED = os.getenv("API_TOKEN", 'NULL')

bot = TeleBot(TOKEN)

# --- FUNZIONE PER CREARE LA TASTIERA ---
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn_import = types.KeyboardButton('🚀 Avvia Importazione')
    btn_status = types.KeyboardButton('🖥️ Stato Server')
    btn_test = types.KeyboardButton('🔌 Test Connessione')
    btn_ministero = types.KeyboardButton('🗄️ Test Dati Ministero')
    markup.add(btn_import, btn_status, btn_test, btn_ministero)
    return markup

# --- COMANDI DI BENVENUTO ---
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if message.chat.id != ADMIN_ID: return
    bot.send_message(
        message.chat.id,
        "🕹️ *Pannello di Controllo Carburanti*\nUsa i tasti qui sotto per gestire il backend.",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

# --- LOGICA TASTO IMPORTAZIONE ---
@bot.message_handler(func=lambda message: message.text == '🚀 Avvia Importazione')
def run_import(message):
    if message.chat.id != ADMIN_ID: return

    bot.send_message(ADMIN_ID, "⏳ *Processo avviato...* controllo nuovi dati dal Ministero.", parse_mode="Markdown")

    try:
        result = subprocess.run([sys.executable, "runimport.py"], capture_output=True, text=True)

        if result.returncode == 0:
            bot.send_message(ADMIN_ID, "✅ *Script eseguito!*\nTra pochi secondi riceverai il report ufficiale.")
        else:
            bot.send_message(ADMIN_ID, f"❌ *Errore Script:*\n`{result.stderr[:500]}`", parse_mode="Markdown")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"💀 *Errore Critico:* {str(e)}")

# --- LOGICA TASTO STATO SERVER ---
@bot.message_handler(func=lambda message: message.text == '🖥️ Stato Server')
def server_status(message):
    if message.chat.id != ADMIN_ID: return

    try:
        # 1. Recupero IP Pubblico
        try:
            ip_pubblico = requests.get('https://api.ipify.org', timeout=5).text
        except:
            ip_pubblico = "Non rilevabile"


        # 2. Info Sistema Operativo (Dinamico)
        os_name = platform.system() # Linux
        os_release = platform.release() # Versione Kernel
        # Per avere il nome della distro (es. Ubuntu) su Linux:
        try:
            distro = subprocess.check_output(["lsb_release", "-ds"]).decode("utf-8").strip()
        except:
            distro = platform.platform()

        try:
            dominio_info = socket.gethostbyaddr(ip_pubblico)
            dominio = dominio_info[0]
        except:
            dominio = 'Non rilevabile'



        hostname = socket.gethostname()

        # 3. Dati Sistema (Uptime, CPU, RAM, Disco)
        uptime_raw = subprocess.check_output(["uptime", "-p"]).decode("utf-8").replace("up ", "")
        cpu_usage = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory()
        disk = shutil.disk_usage("/")

        # 4. Stato API tramite PM2
        api_status = "OFFLINE 🔴"
        try:
            pm2_data = subprocess.check_output(["pm2", "jlist"]).decode("utf-8")
            processes = json.loads(pm2_data)
            for p in processes:
                if p['name'] == "carburanti-api":
                    if p['pm2_env']['status'] == 'online':
                        api_status = "RUNNING 🟢"
                    break
        except:
            api_status = "ERRORE PM2 ⚠️"

        # --- COSTRUZIONE MESSAGGIO FINALE ---
        msg = (
            f"🖥️ *INFO SERVER LINUX*\n\n"
            f"📊 SERVER STATUS: `ACTIVE 🟢` \n\n"
            f"🐧 OS: `{distro}`\n"
            f"⚙️ Kernel: `{os_release}`\n"
            f"🌐 Dominio: `{'prezzicarburanti.app'}`\n"
            f"🌍 IP: `{ip_pubblico}`\n"
            f"🏠 Host: `{hostname}`\n"
            

            f"⏱️ Uptime: `{uptime_raw.strip()}`\n"
            f"🚀 CPU: `{cpu_usage}%`\n"
            f"🧠 RAM: `{ram.percent}%` ({ram.used // (1024 ** 2)}MB / {ram.total // (1024 ** 2)}MB)\n"
            f"💽 Disco: `{(disk.used / disk.total) * 100:.1f}%`\n\n"

            f"🔌 *SERVIZI:*\n"
            f"🌐 API Backend: `{api_status}`\n"
            f"🤖 Bot Control: `RUNNING 🟢`"
        )

        bot.send_message(ADMIN_ID, msg, parse_mode="Markdown")

    except Exception as e:
        bot.send_message(ADMIN_ID, f"⚠️ *Errore Monitoraggio:*\n`{str(e)}`", parse_mode="Markdown")


@bot.message_handler(func=lambda message: message.text == '🔌 Test Connessione')
def run_test(message):
    if message.chat.id != ADMIN_ID: return

    url_to_test = 'https://api.prezzicarburanti.app'

    try:
        # Il bot "bussa" alla porta delle tue API
        response = requests.get(url_to_test, timeout=5)

        if response.status_code == 200:
            # Se risponde 200 OK, tutto è configurato a dovere (DNS, Nginx, SSL, FastAPI)
            msg = "✅ *Connessione OK!*\nIl dominio risponde correttamente."

            # Aggiungiamo un bottone cliccabile per aprirlo TU dal TUO telefono
            markup = types.InlineKeyboardMarkup()
            btn_web = types.InlineKeyboardButton("🌍 Apri nel Browser", url=url_to_test)
            markup.add(btn_web)

            bot.send_message(ADMIN_ID, msg, parse_mode="Markdown", reply_markup=markup)
        else:
            bot.send_message(ADMIN_ID, f"⚠️ *Errore:* Il server ha risposto con codice `{response.status_code}`")

    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ *Errore di Rete:* Impossibile raggiungere il dominio.\n`{str(e)[:100]}`")


@bot.message_handler(func=lambda message: message.text == '🗄️ Test Dati Ministero')
def run_test_dati_min(message):
    if message.chat.id != ADMIN_ID: return

    url_to_test1 = os.getenv("DATA_URL_DISTRIBUTORI")
    url_to_test2 = os.getenv("DATA_URL_PREZZI")

    try:

        response = requests.get(url_to_test1, timeout=5)

        if response.status_code == 200:
            # Se risponde 200 OK, tutto è configurato a dovere (DNS, Nginx, SSL, FastAPI)
            msg = "✅ *Connessione OK!*\nRisorsa " + url_to_test1 + " disponibile."

            # Aggiungiamo un bottone cliccabile per aprirlo TU dal TUO telefono
            markup = types.InlineKeyboardMarkup()
            btn_web = types.InlineKeyboardButton("🌍 Apri nel Browser", url=url_to_test1)
            markup.add(btn_web)

            bot.send_message(ADMIN_ID, msg, parse_mode="Markdown", reply_markup=markup)
        else:
            bot.send_message(ADMIN_ID, f"⚠️ *Errore:* Il server ha risposto con codice `{response.status_code}`")

    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ *Errore di Rete:* Impossibile raggiungere il dominio.\n`{str(e)[:100]}`")


    try:

        response = requests.get(url_to_test2, timeout=5)

        if response.status_code == 200:
            # Se risponde 200 OK, tutto è configurato a dovere (DNS, Nginx, SSL, FastAPI)
            msg = "✅ *Connessione OK!*\nRisorsa " + url_to_test2 + " disponibile."

            # Aggiungiamo un bottone cliccabile per aprirlo TU dal TUO telefono
            markup = types.InlineKeyboardMarkup()
            btn_web = types.InlineKeyboardButton("🌍 Apri nel Browser", url=url_to_test2)
            markup.add(btn_web)

            bot.send_message(ADMIN_ID, msg, parse_mode="Markdown", reply_markup=markup)
        else:
            bot.send_message(ADMIN_ID, f"⚠️ *Errore:* Il server ha risposto con codice `{response.status_code}`")

    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ *Errore di Rete:* Impossibile raggiungere il dominio.\n`{str(e)[:100]}`")





# --- GESTORE MESSAGGI GENERICI ---
@bot.message_handler(func=lambda message: True)
def echo_all(message):
    if message.chat.id == ADMIN_ID:
        bot.reply_to(message, "Usa i bottoni del menu per interagire!", reply_markup=main_keyboard())

# Avvio del Bot
print(f"Bot di controllo in ascolto...")
bot.polling(none_stop=True)

async def verify_api_key(x_api_key: str = Header(None, alias="X-API-KEY")):

    if x_api_key is None:
        raise HTTPException(status_code=401, detail="Header X-API-KEY mancante")
    if x_api_key != API_KEY_EXPECTED:
        raise HTTPException(status_code=401, detail="Accesso negato: Chiave non valida")
    return x_api_key