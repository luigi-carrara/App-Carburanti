from fastapi import FastAPI, HTTPException
import psycopg2
from psycopg2.extras import RealDictCursor
import os

SERVER_IP = os.getenv("SERVER_IP", "localhost")

app = FastAPI()


def get_db_connection():
    # Sostituisci con i tuoi dati reali di Hetzner
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        cursor_factory=RealDictCursor
    )


@app.get("/search")
async def search_distributori(
        lat: float,
        lon: float,
        raggio: int = 5000,
        comune: str | None = None,
        bandiera: str | None = None,
        ricerca: str | None = None,
        limit: int = 50

):

    if limit > 100:
        limit = 100

    conn = get_db_connection()
    cur = conn.cursor()

    # 1. Base della query con calcolo distanza PostGIS
    # Usiamo ST_Distance con ::geography per avere i metri reali
    query = """
        SELECT *, 
        ST_Distance(geom, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography) as distanza_metri
        FROM public.distributors
        WHERE is_active = true AND ST_DWithin(geom, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, %(raggio)s)
    """

    # Dizionario per i parametri SQL
    params = {'lat': lat, 'lon': lon, 'raggio': raggio}

    # 2. Aggiunta dinamica dei filtri opzionali
    if comune:
        query += " AND comune ILIKE %(comune)s"
        params['comune'] = f"%{comune}%"

    if bandiera:
        query += " AND bandiera ILIKE %(bandiera)s"
        params['bandiera'] = f"%{bandiera}%"

    if ricerca:
        # Cerca la parola nel nome dell'impianto o nell'indirizzo
        query += " AND (nome_impianto ILIKE %(ricerca)s OR indirizzo ILIKE %(ricerca)s)"
        params['ricerca'] = f"%{ricerca}%"

    # 3. Ordinamento per distanza e limite di sicurezza
    query += " ORDER BY distanza_metri ASC LIMIT %(limit)s"

    try:
        cur.execute(query, params)
        rows = cur.fetchall()
        return {
            "status": "success",
            "params": {"lat": lat, "lon": lon, "raggio": raggio},
            "total": len(rows),
            "results": rows
        }
    except Exception as e:
        print(f"Errore Database: {e}")
        raise HTTPException(status_code=500, detail="Errore interno al database")
    finally:
        cur.close()
        conn.close()