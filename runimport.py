import requests
import pandas as pd
from datetime import datetime
import psycopg2
from psycopg2 import extensions
from dotenv import load_dotenv
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import logging
import pytz
import traceback
import io

# =========================
# CONFIGURAZIONE TIMEZONE E CARTELLE
# =========================
italy_tz = pytz.timezone('Europe/Rome')


def get_now_it():
    return datetime.now(italy_tz)


load_dotenv("config.env")
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
LOG_DIR = "logs"

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)
if not os.path.exists(BACKUP_DIR):
    os.makedirs(BACKUP_DIR)

# Generiamo un nome file log unico per questa esecuzione
current_timestamp = get_now_it().strftime("%d%m%Y_%H%M")
log_filename = os.path.join(LOG_DIR, f"import_{current_timestamp}.log")



# =========================
# CONFIGURAZIONE LOGGING
# =========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =========================
# CONFIGURAZIONE AMBIENTE
# =========================
DATA_URL_PREZZI = os.getenv("DATA_URL_PREZZI")
DATA_URL_DISTRIBUTORI = os.getenv("DATA_URL_DISTRIBUTORI")
CSV_SEPARATOR = os.getenv("CSV_SEPARATOR", "|")
CSV_ENCODING = os.getenv("CSV_ENCODING", "latin-1")
GG_INACTIVE = int(os.getenv("GG_INACTIVE", 15))

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD")
}

MAIL_ACTIVE = os.getenv("MAIL_ACTIVE") == "1"



# =========================
# FUNZIONI SUPPORTO
# =========================

def get_db_connection(config):
    return psycopg2.connect(**config)


def save_backup(content, prefix):
    timestamp = get_now_it().strftime("%d%m%Y%H%M")
    filename = f"{prefix}_{timestamp}.csv"
    filepath = os.path.join(BACKUP_DIR, filename)
    with open(filepath, "w", encoding=CSV_ENCODING) as f:
        f.write(content)
    logger.info(f"Backup salvato: {filepath}")


def load_csv_with_date(url, backup_prefix):
    headers = {"User-Agent": "Mozilla/5.0 CarburantiApp/5.0"}
    res = requests.get(url, headers=headers, timeout=60)
    if res.status_code != 200:
        raise Exception(f"Errore Download: HTTP {res.status_code}")

    save_backup(res.text, backup_prefix)
    lines = res.text.splitlines()

    d_str = lines[0].replace("Estrazione del ", "").strip() if "Estrazione del" in lines[0] else "2026-01-01"

    # AGGIUNTO: on_bad_lines='skip' e engine='c' (più veloce)
    df = pd.read_csv(
        io.StringIO("\n".join(lines[1:])),
        sep=CSV_SEPARATOR,
        encoding=CSV_ENCODING,
        on_bad_lines='skip',
        engine='c'
    )
    return df, d_str

# =========================
# LOGICA DB
# =========================

def upsert_distributor(cursor, row):


    dist_id = int(row["idImpianto"])
    try:
        lat = float(row["Latitudine"]) if pd.notna(row["Latitudine"]) else None
        lon = float(row["Longitudine"]) if pd.notna(row["Longitudine"]) else None
    except:
        lat, lon = None, None

    cursor.execute("""
        INSERT INTO distributors (id, gestore, bandiera, tipo_impianto, nome_impianto, indirizzo, comune, provincia, lat, lon, is_active, geom)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, TRUE, 
            CASE WHEN %s IS NOT NULL AND %s IS NOT NULL THEN ST_SetSRID(ST_MakePoint(%s, %s), 4326) ELSE NULL END)
        ON CONFLICT (id) DO UPDATE SET gestore = EXCLUDED.gestore, bandiera = EXCLUDED.bandiera, is_active = TRUE,
            lat = COALESCE(EXCLUDED.lat, distributors.lat), lon = COALESCE(EXCLUDED.lon, distributors.lon),
            geom = COALESCE(ST_SetSRID(ST_MakePoint(EXCLUDED.lon, EXCLUDED.lat), 4326), distributors.geom);
    """, (dist_id, row["Gestore"], row["Bandiera"], row["Tipo Impianto"], row["Nome Impianto"],
          row["Indirizzo"], row["Comune"], row["Provincia"], lat, lon, lon, lat, lon, lat))


def upsert_price_from_row(cursor, row):
    dist_id = int(row["idImpianto"])
    fuel = normalize_fuel(row["descCarburante"])

    if not fuel: return None

    try:
        dt = datetime.strptime(row["dtComu"], "%d/%m/%Y %H:%M:%S")
        new_price = float(row["prezzo"])
        is_self = bool(int(row["isSelf"]))

        cursor.execute("""
            INSERT INTO fuel_prices (distributor_id, fuel_type, price, is_self, updated_at, price_trend, price_diff)
            SELECT %s, %s, %s, %s, %s, 0, 0.000
            WHERE EXISTS (SELECT 1 FROM distributors WHERE id = %s)
            ON CONFLICT (distributor_id, fuel_type, is_self) 
            DO UPDATE SET 
                price_trend = CASE 
                    WHEN EXCLUDED.price < fuel_prices.price THEN -1
                    WHEN EXCLUDED.price > fuel_prices.price THEN 1
                    ELSE 0
                END,
                -- CALCOLO DELLA DIFFERENZA: Nuovo prezzo - Vecchio prezzo
                price_diff = EXCLUDED.price - fuel_prices.price,
                price = EXCLUDED.price,
                updated_at = EXCLUDED.updated_at;
        """, (dist_id, fuel, new_price, is_self, dt, dist_id))

        return dist_id if cursor.rowcount > 0 else None
    except Exception as e:
        print(f"Errore: {e}")
        return None


def normalize_fuel(fuel):
    f = str(fuel).lower()
    if "benzina" in f: return "benzina"
    if "gasolio" in f or "diesel" in f: return "diesel"
    if "gpl" in f: return "gpl"
    if "metano" in f: return "metano"
    return None


# =========================
# INVIO EMAIL CON ALLEGATO
# =========================

def send_summary_email(status, start, d_price, d_dist, msg, sec, log_path):
    try:
        sender = os.getenv("SENDER")
        receiver = os.getenv("RECEIVER")
        password = os.getenv("PASSWORD")

        email = MIMEMultipart()
        email["Subject"] = f"App Carburanti {status} - {get_now_it().strftime('%d/%m/%Y')}"
        email["From"], email["To"] = sender, receiver


        body = (
            f"--- REPORT IMPORT CARBURANTI ---\n"
            f"STATO: {status}\n"
            f"DURATA: {sec:.2f} secondi\n\n"
            f"Data Ministero Prezzi: {d_price}\n"
            f"Data Ministero Distributori: {d_dist}\n\n"
            f"LOG FILE ALLEGATO: {os.path.basename(log_path)}"
        )
        email.attach(MIMEText(body, 'plain'))

        if os.path.exists(log_path):
            with open(log_path, "rb") as attachment:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(log_path)}")
                email.attach(part)

        # --- INVIO VIA PORTA 587 (STARTTLS) ---
        logger.info(f"Tentativo invio email via porta 587...")

        # Nota: usiamo SMTP() invece di SMTP_SSL() per la porta 587
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as srv:
            srv.starttls()  # Questo attiva la crittografia sulla connessione aperta
            srv.login(sender, password)
            srv.send_message(email)

        logger.info("Email inviata con successo via porta 587!")
    except Exception as e:
        logger.error(f"Invio mail fallito (porta 587): {e}")


TELEGRAM_ACTIVE = os.getenv("TELEGRAM_ACTIVE") == "1"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ENV = os.getenv("ENV")


def send_telegram_report(status, duration, d_price, d_dist, message, log_path):
    if not TELEGRAM_ACTIVE:
        return


    try:
        # 1. Prepariamo il testo del messaggio
        icon = "✅" if 'SUCCESS' in status else "❌"
        icon_env = "⛽" if ENV == "PROD" else "🛠️"
        text = (
            f"{icon} *REPORT IMPORT CARBURANTI*\n\n"
            f"AMBIENTE: {ENV} {icon_env}\n\n"
            f"*STATO:* {status}\n"
            f"*DURATA:* {duration:.2f}s\n"
            f"*Data Prezzi:* `{d_price}`\n"
            f"*Data Distr:* `{d_dist}`\n\n"
            f"_{message[:200]}..._"  # Tagliamo se il messaggio è troppo lungo
        )

        # 2. Invio Messaggio di Testo
        url_msg = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown"
        }
        requests.post(url_msg, data=data, timeout=20)

        # 3. Invio File Log (se esiste)
        # 3. Invio File Log (se esiste)





        if os.path.exists(log_path):
            url_doc = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
            with open(log_path, "rb") as f:
                display_name = os.path.basename(log_path).replace(".log", ".txt")
                files = {"document": (display_name, f)}
                requests.post(url_doc, data={"chat_id": TELEGRAM_CHAT_ID}, files=files, timeout=30)


        logger.info("Report Telegram inviato con successo!")

    except Exception as e:
        logger.error(f"Invio Telegram fallito: {e}")

# =========================
# MAIN
# =========================

def import_data():
    start_time = get_now_it()
    logger.info(f"AVVIO IMPORTAZIONE: {start_time}")
    status, message = "SUCCESS", "Import completato"
    date_price, date_dist = "N/A", "N/A"



    try:
        df_dist, date_dist = load_csv_with_date(DATA_URL_DISTRIBUTORI, "dati_distributori")
        df_price, date_price = load_csv_with_date(DATA_URL_PREZZI, "dati_prezzi")
        date_price_dt = datetime.strptime(date_price, "%Y-%m-%d")

        conn = get_db_connection(DB_CONFIG)
        with conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT last_price_date FROM system_info WHERE id = 1")
                last_import = cursor.fetchone()
                if last_import and last_import[0] and last_import[0].date() == date_price_dt.date():
                    logger.info("Dataset già aggiornato. Fine.")
                    if TELEGRAM_ACTIVE:
                        send_telegram_report("SUCCESS | SKIPPED", 0, date_price, date_dist, 'Ultima importazione saltata. Dataset già aggiornato', '')
                    return
                
                
                logger.info(f"Elaborazione distributori...")
                for _, row in df_dist.iterrows(): upsert_distributor(cursor, row)

                logger.info(f"Elaborazione prezzi...")
                imported_ids = set()
                for _, row in df_price.iterrows():
                    did = upsert_price_from_row(cursor, row)
                    if did: imported_ids.add(did)

                if imported_ids:
                    cursor.execute("UPDATE distributors SET is_active = TRUE WHERE id = ANY(%s)", (list(imported_ids),))

                cursor.execute(
                    "UPDATE distributors SET is_active = FALSE WHERE id NOT IN (SELECT DISTINCT distributor_id FROM fuel_prices WHERE updated_at > NOW() - (%s || ' days')::interval)",
                    (GG_INACTIVE,))
                cursor.execute("UPDATE system_info SET last_price_date = %s WHERE id = 1", (date_price_dt,))
        conn.close()
    except Exception as e:
        status = "ERROR"
        message = f"ERRORE: {str(e)}\n{traceback.format_exc()}"
        logger.error(message)
        
    finally:
        duration = (get_now_it() - start_time).total_seconds()
        if TELEGRAM_ACTIVE:
            send_telegram_report(status, duration, date_price, date_dist, message, log_filename)
        if MAIL_ACTIVE:
            send_summary_email(status, start_time, date_price, date_dist, message, duration, log_filename)
        logger.info(f"FINE PROCEDURA IN {duration:.2f}s")


if __name__ == "__main__":
    import_data()