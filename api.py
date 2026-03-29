import os
import subprocess
import sys
import socket
import json
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# --- FIX PERCORSO CONFIG.ENV ---
base_path = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(base_path, "config.env")
load_dotenv(config_path)

SERVER_IP = os.getenv("SERVER_IP", "localhost")

app = FastAPI()

def get_db_connection():
    try:
        return psycopg2.connect(
            host=os.getenv("DB_HOST"),
            database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            port=os.getenv("DB_PORT", "5432"),
            cursor_factory=RealDictCursor,
            connect_timeout=5
        )
    except Exception as e:
        print(f"❌ ERRORE CONNESSIONE DB: {e}")
        raise e

# 1. ELENCO BANDIERE
@app.get("/distributors_type")
async def get_distributor_type_list():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = "SELECT DISTINCT UPPER(bandiera) as bandiera FROM public.distributors WHERE bandiera IS NOT NULL ORDER BY bandiera ASC"
        cur.execute(query)
        rows = cur.fetchall()
        bandiere = [r['bandiera'] for r in rows]
        return {"status": "success", "server": SERVER_IP, "data": bandiere}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()

# 2. AUTOSUGGEST
@app.get("/autosuggest")
async def autosuggest(
        target: str = Query(..., description="Cerca in 'comune' o 'bandiera'"),
        q: str = Query(..., description="Testo digitato"),
        limit: int = 10
):
    if target not in ["comune", "bandiera"]:
        raise HTTPException(status_code=400, detail="Target deve essere 'comune' o 'bandiera'")

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        column = "comune" if target == "comune" else "bandiera"
        query = f"SELECT DISTINCT {column} FROM public.distributors WHERE {column} ILIKE %s ORDER BY {column} ASC LIMIT %s"
        cur.execute(query, (f"{q}%", limit))
        rows = cur.fetchall()
        suggestions = [r[column] for r in rows]
        return {"status": "success", "suggestions": suggestions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()

# 3. RICERCA GEOGRAFICA
@app.get("/search")
async def search_distributori(
        lat: float,
        lon: float,
        raggio: int = 5000,
        comune: Optional[str] = None,
        bandiera: Optional[str] = None,
        ricerca: Optional[str] = None,
        is_self: Optional[bool] = None,
        prezzo_max: Optional[float] = None,
        limit: int = 50
):
    if limit > 100:
        limit = 100

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Usiamo la Primary Key (d.id) nel GROUP BY per permettere d.*
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

        params = {'lat': lat, 'lon': lon, 'raggio': raggio}

        if comune:
            query += " AND d.comune ILIKE %(comune)s"
            params['comune'] = f"%{comune}%"

        if bandiera:
            query += " AND d.bandiera ILIKE %(bandiera)s"
            params['bandiera'] = f"%{bandiera}%"

        if ricerca:
            query += " AND (d.nome_impianto ILIKE %(ricerca)s OR d.indirizzo ILIKE %(ricerca)s)"
            params['ricerca'] = f"%{ricerca}%"

        if is_self is not None:
            query += " AND p.is_self = %(is_self)s"
            params['is_self'] = is_self

        if prezzo_max is not None:
            query += " AND p.price <= %(prezzo_max)s"
            params['prezzo_max'] = prezzo_max

        # GROUP BY corretto: raggruppiamo per ID e per la distanza calcolata
        query += " GROUP BY d.id, d.gestore, d.bandiera, d.tipo_impianto, d.nome_impianto, d.indirizzo, d.comune, d.provincia, d.lat, d.lon, d.is_active, d.geom, d.distanza_metri"
        query += " ORDER BY d.distanza_metri ASC LIMIT %(limit)s"
        params['limit'] = limit

        cur.execute(query, params)
        rows = cur.fetchall()

        return {
            "status": "success",
            "server": SERVER_IP,
            "results": rows
        }

    except Exception as e:
        print(f"Errore Database: {e}")
        raise HTTPException(status_code=500, detail=f"Errore interno: {str(e)}")
    finally:
        if conn:
            conn.close()