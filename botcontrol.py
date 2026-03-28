import os
import subprocess
import sys
from telebot import TeleBot, types  # Importiamo 'types' per i bottoni
from dotenv import load_dotenv
import json


load_dotenv("config.env")
TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("TELEGRAM_CHAT_ID"))

bot = TeleBot(TOKEN)


# --- FUNZIONE PER CREARE LA TASTIERA ---
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn_import = types.KeyboardButton('🚀 Avvia Importazione')
    btn_status = types.KeyboardButton('🖥️ Stato Server')
    markup.add(btn_import, btn_status)
    return markup


@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if message.chat.id != ADMIN_ID: return
    bot.send_message(
        message.chat.id,
        "🕹️ *Pannello di Controllo Carburanti*\nUsa i tasti qui sotto per gestire il backend.",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )


@bot.message_handler(func=lambda message: message.text == '🚀 Avvia Importazione')
def run_import(message):
    if message.chat.id != ADMIN_ID: return

    bot.send_message(ADMIN_ID, "⏳ *Processo avviato...* controllo nuovi dati dal Ministero.", parse_mode="Markdown")

    try:
        # Lanciamo lo script di import (assicurati che il nome file sia corretto)
        result = subprocess.run([sys.executable, "runimport.py"], capture_output=True, text=True)

        if result.returncode == 0:
            bot.send_message(ADMIN_ID, "✅ *Script eseguito!*\nTra pochi secondi riceverai il report ufficiale.")
        else:
            bot.send_message(ADMIN_ID, f"❌ *Errore Script:*\n`{result.stderr[:500]}`", parse_mode="Markdown")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"💀 *Errore Critico:* {str(e)}")


@bot.message_handler(func=lambda message: message.text == '🖥️ Stato Server')
def server_status(message):
    if message.chat.id != ADMIN_ID: return

    try:
        # 1. Recuperiamo l'Uptime del sistema
        uptime = subprocess.check_output(["uptime", "-p"]).decode("utf-8").replace("up ", "")

        # 2. Interroghiamo PM2 per lo stato dell'API
        # Sostituisci 'carburanti-api' con il nome esatto che vedi in 'pm2 list'
        api_process_name = "carburanti-api"
        pm2_data = subprocess.check_output(["pm2", "jlist"]).decode("utf-8")
        processes = json.loads(pm2_data)

        # Cerchiamo il processo specifico
        api_status = "OFFLINE 🔴"
        for p in processes:
            if p['name'] == api_process_name:
                status = p['pm2_env']['status']
                if status == 'online':
                    api_status = "RUNNING 🟢"
                break

        # 3. Componiamo il messaggio finale
        msg = (
            f"🖥️ *INFO SERVER*\n"
            f"⏱️ Uptime: `{uptime.strip()}`\n\n"
            f"🔌 *SERVIZI:*\n"
            f"🌐 API Backend: `{api_status}`\n"
            f"🤖 Bot Control: `RUNNING 🟢`"  # Se stai leggendo questo, il bot è vivo!
        )

        bot.send_message(ADMIN_ID, msg, parse_mode="Markdown")

    except Exception as e:
        bot.send_message(ADMIN_ID, f"⚠️ *Errore Monitoraggio:*\n`{str(e)}`", parse_mode="Markdown")
@bot.message_handler(func=lambda message: True)
def echo_all(message):
    if message.chat.id == ADMIN_ID:
        bot.reply_to(message, "Usa i bottoni del menu per interagire!", reply_markup=main_keyboard())


print("Bot di controllo con bottoni in ascolto...")
bot.polling()