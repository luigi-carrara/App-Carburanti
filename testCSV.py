import requests
import pandas as pd
from datetime import datetime
import psycopg2
from psycopg2 import extensions
from dotenv import load_dotenv
import os
import smtplib
from email.mime.text import MIMEText
import logging
from logging.handlers import RotatingFileHandler

# =========================
# CONFIGURAZIONE LOGGING (Con Rotazione)
# =========================
log_filename = "import.main.log"
# Rotazione: 10MB per file, tiene gli ultimi 5 backup
log_handler = RotatingFileHandler(
    log_filename,
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding='utf-8'
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        log_handler,
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =========================
# CONFIGURAZIONE AMBIENTE
# =========================
load_dotenv("config.env")

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
# FUNZIONI DATABASE & MANUTENZIONE
# =========================

def get_db_connection(config):
    return psycopg2.connect(**config)


def vacuum_db(config):
    """Pulisce i residui fisici del DB (fuori dalla transazione)"""
    try:
        conn = psycopg2.connect(**config)
        conn.set_isolation_level(extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        logger.info("Ottimizzazione fisica tabelle (VACUUM ANALYZE)...")
        cursor.execute("VACUUM ANALYZE distributors;")
        cursor.execute("VACUUM ANALYZE fuel_prices;")
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"Errore durante VACUUM: {e}")


def get_last_import_date(cursor):
    cursor.execute("SELECT last_price_date FROM system_info WHERE id = 1")
    result = cursor.fetchone()
    return result[0] if result else None


# =========================
# LOGICA DI IMPORTAZIONE
# =========================

def import_data():
    start_time = datetime.now()
    logger.info("==================================================")
    logger.info(f"AVVIO IMPORTAZIONE IN DATA { str(datetime.now())} - Soglia inattività: {GG_INACTIVE}gg")

    status = "SUCCESS"
    message = "Import completato correttamente"
    date_price, date_dist = "N/A", "N/A"

    try:
        # 1. Download CSV
        df_dist, date_dist = load_csv_with_date(DATA_URL_DISTRIBUTORI)
        df_price, date_price = load_csv_with_date(DATA_URL_PREZZI)
        date_price_dt = datetime.strptime(date_price, "%Y-%m-%d")

        conn = get_db_connection(DB_CONFIG)
        with conn:  # Gestione transazione atomica
            with conn.cursor() as cursor:

                # Check aggiornamento
                last_import = get_last_import_date(cursor)
                if last_import and last_import.date() == date_price_dt.date():
                    logger.info("Dataset già aggiornato. Procedura interrotta.")
                    return

                # 2. Import Distributori (PostGIS gestito da Python)
                logger.info(f"Elaborazione {len(df_dist)} distributori...")
                for _, row in df_dist.iterrows():
                    upsert_distributor(cursor, row)

                # 3. Import Prezzi
                logger.info(f"Elaborazione {len(df_price)} prezzi...")
                imported_ids = set()
                for _, row in df_price.iterrows():
                    did = upsert_price_from_row(cursor, row)
                    if did:
                        imported_ids.add(did)

                # 4. Gestione is_active
                if imported_ids:
                    cursor.execute("UPDATE distributors SET is_active = TRUE WHERE id = ANY(%s)", (list(imported_ids),))

                logger.info(f"Esecuzione cleanup inattivi (>{GG_INACTIVE}gg)...")
                cursor.execute("""
                    UPDATE distributors 
                    SET is_active = FALSE 
                    WHERE id NOT IN (
                        SELECT DISTINCT distributor_id 
                        FROM fuel_prices 
                        WHERE updated_at > NOW() - (%s || ' days')::interval
                    )
                """, (GG_INACTIVE,))

                # 5. Update data sistema
                cursor.execute("UPDATE system_info SET last_price_date = %s WHERE id = 1", (date_price_dt,))

        conn.close()
        # 6. Manutenzione post-commit
        vacuum_db(DB_CONFIG)

    except Exception as e:
        status = "ERROR"
        message = f"Errore Critico: {str(e)}"
        logger.error(message)

    finally:
        duration_sec = (datetime.now() - start_time).total_seconds()
        if MAIL_ACTIVE:
            send_summary_email(status, start_time, date_price, date_dist, message, duration_sec)
        logger.info(f"PROCEDURA TERMINATA ALLE {datetime.now()} IN: {duration_sec:.2f} secondi")
        logger.info("==================================================")


# =========================
# FUNZIONI DI SUPPORTO
# =========================

def load_csv_with_date(url):
    headers = {"User-Agent": "Mozilla/5.0 CarburantiApp/2.0"}
    res = requests.get(url, headers=headers, timeout=60)
    if res.status_code != 200:
        raise Exception(f"HTTP {res.status_code} su {url}")

    lines = res.text.splitlines()
    d_str = lines[0].replace("Estrazione del ", "").strip() if "Estrazione del" in lines[0] else "2026-01-01"
    df = pd.read_csv(pd.io.common.StringIO("\n".join(lines[1:])), sep=CSV_SEPARATOR, encoding=CSV_ENCODING)
    return df, d_str


def upsert_distributor(cursor, row):
    dist_id = int(row["idImpianto"])
    lat = float(row["Latitudine"]) if pd.notna(row["Latitudine"]) else None
    lon = float(row["Longitudine"]) if pd.notna(row["Longitudine"]) else None

    # ST_MakePoint(LON, LAT) -> Nota: Lon prima di Lat in PostGIS
    cursor.execute("""
        INSERT INTO distributors (
            id, gestore, bandiera, tipo_impianto, nome_impianto, 
            indirizzo, comune, provincia, lat, lon, is_active, geom
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, TRUE, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
        ON CONFLICT (id) DO UPDATE SET
            gestore = EXCLUDED.gestore, 
            bandiera = EXCLUDED.bandiera, 
            is_active = TRUE, 
            lat = COALESCE(distributors.lat, EXCLUDED.lat), 
            lon = COALESCE(distributors.lon, EXCLUDED.lon),
            geom = COALESCE(distributors.geom, ST_SetSRID(ST_MakePoint(EXCLUDED.lon, EXCLUDED.lat), 4326));
    """, (dist_id, row["Gestore"], row["Bandiera"], row["Tipo Impianto"], row["Nome Impianto"],
          row["Indirizzo"], row["Comune"], row["Provincia"], lat, lon, lon, lat))


def upsert_price_from_row(cursor, row):
    dist_id = int(row["idImpianto"])
    fuel = normalize_fuel(row["descCarburante"])
    if not fuel: return None

    try:
        dt = datetime.strptime(row["dtComu"], "%d/%m/%Y %H:%M:%S")
        cursor.execute("""
            INSERT INTO fuel_prices (distributor_id, fuel_type, price, is_self, updated_at)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (distributor_id, fuel_type, is_self) 
            DO UPDATE SET price = EXCLUDED.price, updated_at = EXCLUDED.updated_at;
        """, (dist_id, fuel, float(row["prezzo"]), bool(int(row["isSelf"])), dt))
        return dist_id
    except:
        return None


def normalize_fuel(fuel):
    f = fuel.lower()
    if "benzina" in f: return "benzina"
    if "gasolio" in f or "diesel" in f: return "diesel"
    if "gpl" in f: return "gpl"
    if "metano" in f: return "metano"
    return None


def send_summary_email(status, start, d_price, d_dist, msg, sec):
    try:
        end = datetime.now()
        sender = os.getenv("SENDER")
        receiver = os.getenv("RECEIVER")

        body = (
            f"REPORT Import App Carburanti---\n"
            f"STATO: {status}\n"
            f"Inizio: {start.strftime('%H:%M:%S')}\n"
            f"Fine: {end.strftime('%H:%M:%S')}\n"
            f"DURATA: {sec:.2f} secondi\n\n"
            f"Data Ministero Prezzi: {d_price}\n"
            f"Data Ministero Distributori: {d_dist}\n\n"
            f"DETTAGLIO:\n{msg}"
        )

        email = MIMEText(body)
        email["Subject"] = f"REPORT Import App Carburanti {status} - {end.strftime('%d/%m/%Y')}"
        email["From"], email["To"] = sender, receiver

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(sender, os.getenv("PASSWORD"))
            srv.send_message(email)
    except Exception as e:
        logger.error(f"Invio mail fallito: {e}")


if __name__ == "__main__":
    import_data()