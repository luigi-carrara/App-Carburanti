import os
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Query, Header, Depends
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# --- CONFIGURAZIONE ---
base_path = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(base_path, "config.env")
load_dotenv(config_path)

SERVER_IP = os.getenv("SERVER_IP", "localhost")
API_KEY_EXPECTED = os.getenv("API_TOKEN", 'NULL')

app = FastAPI(title="API Prezzi Carburanti")


async def verify_api_key(x_api_key: str = Header(None, alias="X-API-KEY")):

    if x_api_key is None:
        raise HTTPException(status_code=401, detail="Header X-API-KEY mancante")
    if x_api_key != API_KEY_EXPECTED:
        raise HTTPException(status_code=401, detail="Accesso negato: Chiave non valida")
    return x_api_key


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT", "5432"),
        cursor_factory=RealDictCursor
    )


# --- ROTTE API ---

# 1. Elenco Bandiere (per filtri)
@app.get("/distributors_type")
async def get_distributor_type_list(token: str = Depends(verify_api_key)):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = "SELECT DISTINCT UPPER(bandiera) as bandiera FROM public.distributors WHERE bandiera IS NOT NULL ORDER BY bandiera ASC"
        cur.execute(query)
        bandiere = [r['bandiera'] for r in cur.fetchall()]
        return {"status": "success", "data": bandiere}
    finally:
        if conn: conn.close()


# 2. Autosuggest (Comune o Bandiera)
@app.get("/autosuggest")
async def autosuggest(
        target: str = Query(..., description="Cerca in 'comune' o 'bandiera'"),
        q: str = Query(..., description="Testo digitato"),
        limit: int = 10,
        token: str = Depends(verify_api_key)
):
    if target not in ["comune", "bandiera"]:
        raise HTTPException(status_code=400, detail="Target non valido")

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        column = "comune" if target == "comune" else "bandiera"
        query = f"SELECT DISTINCT {column} FROM public.distributors WHERE {column} ILIKE %s ORDER BY {column} ASC LIMIT %s"
        cur.execute(query, (f"{q}%", limit))
        suggestions = [r[column] for r in cur.fetchall()]
        return {"status": "success", "suggestions": suggestions}
    finally:
        if conn: conn.close()


# 3. Ricerca Geografica Principale
@app.get("/search")
async def search_distributori(
        lat: float, lon: float, raggio: int = 5000,
        comune: Optional[str] = None,
        bandiera: Optional[str] = None,
        ricerca: Optional[str] = None,
        is_self: Optional[bool] = None,
        prezzo_max: Optional[float] = None,
        fuel_type: Optional[str] = None,
        limit: int = 50,
        token: str = Depends(verify_api_key)
):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        query = """
            WITH dist_vicini AS (
                SELECT *, 
                ST_Distance(geom, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography) as distanza_metri
                FROM public.distributors
                WHERE is_active = true 
                AND ST_DWithin(geom, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, %(raggio)s)
            )
            SELECT 
                d.*, 
                json_agg(json_build_object(
                    'fuel_type', p.fuel_type,
                    'price', p.price,
                    'is_self', p.is_self,
                    'updated_at', p.updated_at
                )) FILTER (WHERE p.fuel_type IS NOT NULL) as prezzi
            FROM dist_vicini d
            LEFT JOIN public.fuel_prices p ON d.id = p.distributor_id
            WHERE 1=1
        """
        params = {'lat': lat, 'lon': lon, 'raggio': raggio, 'limit': limit}

        if comune:
            query += " AND d.comune ILIKE %(comune)s";
            params['comune'] = f"%{comune}%"
        if bandiera:
            query += " AND UPPER(d.bandiera) ILIKE %(bandiera)s";
            params['bandiera'] = f"%{bandiera}%"
        if ricerca:
            query += " AND (d.nome_impianto ILIKE %(ricerca)s OR d.indirizzo ILIKE %(ricerca)s)";
            params['ricerca'] = f"%{ricerca}%"
        if is_self is not None:
            query += " AND p.is_self = %(is_self)s";
            params['is_self'] = is_self
        if prezzo_max is not None:
            query += " AND p.price <= %(prezzo_max)s";
            params['prezzo_max'] = prezzo_max
        if fuel_type is not None:
            query += " AND UPPER(p.fuel_type) == %(fuel_type)s";
            params['fuel_type'] = fuel_type

        query += " GROUP BY d.id, d.gestore, d.bandiera, d.tipo_impianto, d.nome_impianto, d.indirizzo, d.comune, d.provincia, d.lat, d.lon, d.is_active, d.geom, d.distanza_metri"
        query += " ORDER BY d.distanza_metri ASC LIMIT %(limit)s"

        cur.execute(query, params)
        return {"status": "success", "results": cur.fetchall()}
    finally:
        if conn: conn.close()

#Tipi carburanti (per popolare combobox)
@app.get("/get_fuel_type")
async def get_fuel_type(
        token: str = Depends(verify_api_key)
):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = "select distinct UPPER(fuel_type) AS fuel_type from fuel_prices ORDER BY fuel_type ASC"
        cur.execute(query)

        # MODIFICA QUI: Trasforma ogni stringa in un dizionario
        # Prima: fuels = [r['fuel_type'] for r in cur.fetchall()]
        fuels = [{"fuel_type": r['fuel_type']} for r in cur.fetchall()]

        return {"status": "success", "fuelTypeList": fuels}
    finally:
        if conn: conn.close()


@app.get("/get_top10d")
async def get_top10d(#Ottengo l'elenco deii 10 gestori con più pompe
        token: str = Depends(verify_api_key)
):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = "SELECT UPPER(bandiera) AS bandiera, COUNT(*) AS conteggio FROM distributors GROUP BY UPPER(bandiera) ORDER BY conteggio DESC LIMIT 10"
        cur.execute(query)


        fuels = [{"bandiera": r['bandiera']} for r in cur.fetchall()]

        return {"status": "success", "bandiera": fuels}
    finally:
        if conn: conn.close()

