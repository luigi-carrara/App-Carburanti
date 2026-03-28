from fastapi import FastAPI, HTTPException, Query
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from typing import Optional


SERVER_IP = os.getenv("SERVER_IP", "localhost")

app = FastAPI()


def get_db_connection():
    # Assicurati che queste variabili siano settate nel tuo env o nel file .bashrc
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        cursor_factory=RealDictCursor
    )


# 1. ELENCO BANDIERE PER COMBOBOX
@app.get("/distributors_type")
async def get_distributor_type_list():
    """Restituisce la lista unica di tutte le bandiere (brand) nel DB."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        query = "SELECT DISTINCT UPPER(bandiera) as bandiera FROM public.distributors WHERE bandiera IS NOT NULL ORDER BY bandiera ASC"
        cur.execute(query)
        rows = cur.fetchall()
        bandiere = [r['bandiera'] for r in rows]
        return {"status": "success", "server": SERVER_IP, "data": bandiere}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


# 2. AUTOSUGGEST (COMUNE O BANDIERA)
@app.get("/autosuggest")
async def autosuggest(
        target: str = Query(..., description="Cerca in 'comune' o 'bandiera'"),
        q: str = Query(..., description="Testo digitato"),
        limit: int = 10
):
    """Suggerisce comuni o brand mentre l'utente scrive."""
    if target not in ["comune", "bandiera"]:
        raise HTTPException(status_code=400, detail="Target deve essere 'comune' o 'bandiera'")

    conn = get_db_connection()
    cur = conn.cursor()

    # Protezione nomi colonne e ricerca case-insensitive
    column = "comune" if target == "comune" else "bandiera"
    query = f"SELECT DISTINCT {column} FROM public.distributors WHERE {column} ILIKE %s ORDER BY {column} ASC LIMIT %s"

    try:
        cur.execute(query, (f"{q}%", limit))
        rows = cur.fetchall()
        suggestions = [r[column] for r in rows]
        return {"status": "success", "suggestions": suggestions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


# 3. RICERCA GEOGRAFICA CON FILTRI
@app.get("/search")
async def search_distributori(
        lat: float,
        lon: float,
        raggio: int = 5000,
        comune: Optional[str] = None,
        bandiera: Optional[str] = None,
        ricerca: Optional[str] = None,
        limit: int = 50
):
    """Ricerca avanzata con PostGIS e filtri dinamici."""
    if limit > 100:
        limit = 100

    conn = get_db_connection()
    cur = conn.cursor()

    # Query base con ST_Distance (usiamo geography per metri precisi)
    query = """
        SELECT *, 
        ST_Distance(geom, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography) as distanza_metri
        FROM public.distributors
        WHERE is_active = true 
        AND ST_DWithin(geom, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, %(raggio)s)
    """

    params = {'lat': lat, 'lon': lon, 'raggio': raggio}

    # Aggiunta filtri dinamici
    if comune:
        query += " AND comune ILIKE %(comune)s"
        params['comune'] = f"%{comune}%"

    if bandiera:
        query += " AND bandiera ILIKE %(bandiera)s"
        params['bandiera'] = f"%{bandiera}%"

    if ricerca:
        query += " AND (nome_impianto ILIKE %(ricerca)s OR indirizzo ILIKE %(ricerca)s)"
        params['ricerca'] = f"%{ricerca}%"

    query += " ORDER BY distanza_metri ASC LIMIT %(limit)s"
    params['limit'] = limit

    try:
        cur.execute(query, params)
        rows = cur.fetchall()
        return {
            "status": "success",
            "server": SERVER_IP,
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