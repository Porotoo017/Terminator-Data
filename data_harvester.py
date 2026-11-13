import sqlite3
import requests
import logging
import os
import time
import random
import io
import re
import pandas as pd
from bs4 import BeautifulSoup, Comment
from typing import List, Tuple, Dict, Any 
from playwright.sync_api import sync_playwright

# --- CONFIGURACIÓN DE LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] - %(message)s",
    handlers=[logging.StreamHandler()]
)

# --- CONFIGURACIÓN DE ENTORNO ---
DB_PATH = "data/quant_master.db"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0"
]

# --- CONFIGURACIÓN DE LIGAS (GLOBAL) ---
LIGAS_CONFIG = {
    # Europa Top
    "Premier-League": 9,
    "La-Liga": 12,
    "Serie-A": 11,
    "Bundesliga": 20,
    "Ligue-1": 13,
    # Europa Copas
    "Champions-League": 8,
    "Europa-League": 19,
    "Conference-League": 882,
    # Europa Segunda División
    "Championship": 10,
    "LaLiga-2": 17,
    "Serie-B": 18,
    "Bundesliga-2": 33,
    "Eredivisie": 23,
    # Sudamérica
    "Primera-Division": 21,       # Liga Profesional Argentina
    "Primera-Nacional": 22,       # Primera Nacional B Argentina
    "Serie-A-Brasil": 24,         # Brasileirao
    "Copa-Libertadores": 14,
    "Copa-Sudamericana": 15,
    "Copa-Argentina": 917,
    # Norteamérica
    "Liga-MX": 31,
    "MLS": 22
}

# Temporadas según el formato de cada liga
TEMPORADAS_ANUALES = ["2023", "2022", "2021", "2020", "2019"]
TEMPORADAS_BIANUALES = ["2023-2024", "2022-2023", "2021-2022", "2020-2021", "2019-2020"]

WIKI_URLS = {
    # Europa
    "Premier-League": "https://es.wikipedia.org/wiki/Premier_League_{}-{}",
    "La-Liga": "https://es.wikipedia.org/wiki/Primera_División_de_España_{}-{}",
    "Serie-A": "https://es.wikipedia.org/wiki/Serie_A_(Italia)_{}-{}",
    "Bundesliga": "https://es.wikipedia.org/wiki/1._Bundesliga_{}-{}",
    "Ligue-1": "https://es.wikipedia.org/wiki/Ligue_1_{}-{}",
    "Champions-League": "https://es.wikipedia.org/wiki/Liga_de_Campeones_de_la_UEFA_{}-{}",
    "Europa-League": "https://es.wikipedia.org/wiki/Liga_Europa_de_la_UEFA_{}-{}",
    "Conference-League": "https://es.wikipedia.org/wiki/Liga_Conferencia_de_la_UEFA_{}-{}",
    "Championship": "https://es.wikipedia.org/wiki/EFL_Championship_{}-{}",
    "LaLiga-2": "https://es.wikipedia.org/wiki/Segunda_División_de_España_{}-{}",
    "Serie-B": "https://es.wikipedia.org/wiki/Serie_B_(Italia)_{}-{}",
    "Bundesliga-2": "https://es.wikipedia.org/wiki/2._Bundesliga_{}-{}",
    "Eredivisie": "https://es.wikipedia.org/wiki/Eredivisie_{}-{}",
    # Sudamérica (Formato Anual)
    "Primera-Division": "https://es.wikipedia.org/wiki/Campeonato_de_Primera_División_{}_(Argentina)",
    "Primera-Nacional": "https://es.wikipedia.org/wiki/Campeonato_de_Primera_Nacional_{}_(Argentina)",
    "Serie-A-Brasil": "https://es.wikipedia.org/wiki/Campeonato_Brasileño_de_Serie_A_{}",
    "Copa-Libertadores": "https://es.wikipedia.org/wiki/Copa_Libertadores_de_América_{}",
    "Copa-Sudamericana": "https://es.wikipedia.org/wiki/Copa_Sudamericana_{}",
    "Copa-Argentina": "https://es.wikipedia.org/wiki/Copa_Argentina_{}",
    # Norteamérica
    "Liga-MX": "https://es.wikipedia.org/wiki/Liga_MX_{}",
    "MLS": "https://es.wikipedia.org/wiki/Major_League_Soccer_{}"
}

EQUIPOS_MAPEO = {
    "manchester utd": "manchester united", "man utd": "manchester united",
    "tottenham": "tottenham hotspur", "wolves": "wolverhampton wanderers",
    "newcastle": "newcastle united", "nottingham": "nottingham forest",
    "boca": "boca juniors", "river": "river plate", "real madrid cf": "real madrid",
    "fc barcelona": "barcelona", "bayern munich": "bayern munchen",
    "inter": "inter milan", "ac milan": "milan", "paris sg": "psg",
    "estudiantes de la plata": "estudiantes", "velez sarsfield": "velez",
    "sao paulo": "sao paulo", "palmeiras": "palmeiras"
}

class HarvesterDatabase:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS historial_equipos (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        equipo TEXT NOT NULL, liga TEXT NOT NULL, temporada TEXT NOT NULL,
                        gf INTEGER, gc INTEGER, xg REAL, xga REAL, possession REAL, fuente TEXT,
                        UNIQUE(equipo, liga, temporada)
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cache_xg (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        equipo TEXT UNIQUE NOT NULL, xg REAL NOT NULL, xga REAL NOT NULL,
                        actualizado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()
                logging.info("Esquema de Base de Datos Harvester inicializado correctamente.")
        except sqlite3.Error as e:
            logging.error(f"Error crítico inicializando esquema DB: {e}")

    def bulk_insert_historial(self, registros: List[Tuple]) -> None:
        if not registros: return
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany("""
                    INSERT OR REPLACE INTO historial_equipos (equipo, liga, temporada, gf, gc, xg, xga, possession, fuente)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, registros)
                conn.commit()
                logging.info(f"Bulk Insert exitoso: {len(registros)} registros procesados.")
        except sqlite3.Error as e:
            logging.error(f"Error en Bulk Insert: {e}")

    def actualizar_cache_xg_global(self) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT equipo, AVG(xg), AVG(xga) FROM historial_equipos 
                    WHERE xg IS NOT NULL AND xga IS NOT NULL GROUP BY equipo
                """)
                filas = cursor.fetchall()
                lote_cache = [(equipo, round(xg_avg, 2), round(xga_avg, 2)) for equipo, xg_avg, xga_avg in filas]
                
                if lote_cache:
                    conn.executemany("""
                        INSERT INTO cache_xg (equipo, xg, xga, actualizado_en)
                        VALUES (?, ?, ?, datetime('now'))
                        ON CONFLICT(equipo) DO UPDATE SET 
                            xg=excluded.xg, xga=excluded.xga, actualizado_en=datetime('now')
                    """, lote_cache)
                    conn.commit()
                    logging.info(f"Caché xG global actualizada para {len(lote_cache)} equipos.")
        except sqlite3.Error as e:
            logging.error(f"Error actualizando caché xG global: {e}")

class DataHarvester:
    def __init__(self, db: HarvesterDatabase):
        self.db = db

    @staticmethod
    def normalizar(nombre: str) -> str:
        limpio = re.sub(r'[^\w\s]', '', nombre).lower().strip()
        return EQUIPOS_MAPEO.get(limpio, limpio)

    def extraer_wikipedia_fallback(self, liga: str, temporada: str) -> List[Tuple]:
        registros = []
        try:
            url_template = WIKI_URLS.get(liga, "")
            if not url_template: return []
            
            # Lógica para manejar temporadas Bianuales (2023-2024) o Anuales (2023)
            if "-" in temporada:
                anio_ini, anio_fin = temporada.split("-")
                anio_fin_corto = anio_fin[2:]
                try:
                    url = url_template.format(anio_ini, anio_fin_corto)
                except IndexError:
                    url = url_template.format(anio_ini)
            else:
                url = url_template.format(temporada)
                
            logging.info(f"Intentando Fallback Wikipedia: {url}")
            
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status() 
            
            tables = pd.read_html(io.StringIO(response.text))
            for df in tables:
                cols = {str(c).lower(): c for c in df.columns}
                col_eq = next((cols[c] for c in cols if 'equip' in c or 'club' in c), None)
                col_gf = next((cols[c] for c in cols if 'gf' in c or 'goles a favor' in c), None)
                col_gc = next((cols[c] for c in cols if 'gc' in c or 'goles en contra' in c), None)
                
                if col_eq and col_gf and col_gc:
                    for _, row in df.iterrows():
                        try:
                            equipo = self.normalizar(str(row[col_eq]))
                            if equipo == 'nan': continue
                            gf = int(row[col_gf]) if str(row[col_gf]).isdigit() else 0
                            gc = int(row[col_gc]) if str(row[col_gc]).isdigit() else 0
                            
                            xg_fb = max(round(gf / 38.0, 2), 0.8)
                            xga_fb = max(round(gc / 38.0, 2), 0.8)
                            
                            registros.append((equipo, liga, temporada, gf, gc, xg_fb, xga_fb, 50.0, "Wikipedia"))
                        except (IndexError, ValueError):
                            continue
                    break
            logging.info(f"Wikipedia Fallback rescató {len(registros)} registros para {liga} {temporada}.")
        except Exception as e:
            logging.warning(f"Wikipedia Fallback también falló para {liga} {temporada}: {e}")
        return registros

    def cosechar_temporada(self, liga_nombre: str, liga_id: int, temporada: str) -> List[Tuple]:
        url = f"https://fbref.com/en/comps/{liga_id}/{temporada}/{temporada}-{liga_nombre}-Stats"
        registros = []
        
        for intento in range(3):
            ua = random.choice(USER_AGENTS)
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        headless=True,
                        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
                    )
                    context = browser.new_context(
                        user_agent=ua,
                        viewport={"width": 1920, "height": 1080},
                        bypass_csp=True,
                        ignore_https_errors=True
                    )
                    page = context.new_page()
                    
                    logging.info(f"Extrayendo [{liga_nombre}] Temporada [{temporada}] (Intento {intento+1})...")
                    page.goto(url, timeout=90000, wait_until="domcontentloaded")
                    page.wait_for_timeout(5000) 
                    
                    html_completo = page.content()
                    browser.close()
                    
                    soup = BeautifulSoup(html_completo, 'lxml')
                    tabla_html = soup.find('table', {'id': 'stats_squads_standard_for'})
                    
                    if not tabla_html:
                        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
                            if 'stats_squads_standard_for' in comment:
                                soup_comment = BeautifulSoup(comment, 'lxml')
                                tabla_html = soup_comment.find('table', {'id': 'stats_squads_standard_for'})
                                if tabla_html: break

                    if tabla_html:
                        df = pd.read_html(io.StringIO(str(tabla_html)))[0]
                        if isinstance(df.columns, pd.MultiIndex):
                            df.columns = df.columns.droplevel(0)
                        
                        cols = {str(c).lower(): c for c in df.columns}
                        col_squad = next((cols[c] for c in cols if 'squad' in c), None)
                        col_gf = next((cols[c] for c in cols if c == 'gf'), None)
                        col_ga = next((cols[c] for c in cols if c == 'ga' or c == 'gc'), None)
                        col_xg = next((cols[c] for c in cols if c == 'xg'), None)
                        col_xga = next((cols[c] for c in cols if c == 'xga'), None)
                        col_poss = next((cols[c] for c in cols if 'poss' in c), None)

                        if col_squad and col_gf and col_ga:
                            for _, row in df.iterrows():
                                try:
                                    raw_name = str(row[col_squad])
                                    if not raw_name or raw_name.lower() == 'nan' or 'vs' in raw_name.lower(): continue
                                    
                                    equipo = self.normalizar(raw_name)
                                    gf = int(row[col_gf]) if pd.notna(row[col_gf]) else 0
                                    gc = int(row[col_ga]) if pd.notna(row[col_ga]) else 0
                                    
                                    xg_val = float(row[col_xg]) if (col_xg and pd.notna(row[col_xg])) else 0.0
                                    xga_val = float(row[col_xga]) if (col_xga and pd.notna(row[col_xga])) else 0.0
                                    
                                    if xg_val <= 0.0: xg_val = max(round(gf / 38.0, 2), 0.8)
                                    if xga_val <= 0.0: xga_val = max(round(gc / 38.0, 2), 0.8)
                                        
                                    poss = float(row[col_poss]) if (col_poss and pd.notna(row[col_poss])) else 50.0
                                    
                                    registros.append((equipo, liga_nombre, temporada, gf, gc, xg_val, xga_val, poss, "FBref"))
                                except (ValueError, TypeError): continue
                        break
            except Exception as e:
                logging.warning(f"Cloudflare/Error en {liga_nombre} {temporada} (Intento {intento+1}): {e}")
                time.sleep(random.uniform(10, 20))
                
        if not registros:
            logging.warning(f"Activando Fallback de Wikipedia para {liga_nombre} {temporada}...")
            registros = self.extraer_wikipedia_fallback(liga_nombre, temporada)
            
        return registros

    def ejecutar_cosecha_masiva(self) -> None:
        logging.info("=== INICIANDO COSECHA MASIVA HISTÓRICA (DATA HARVESTER V3 - GLOBAL) ===")
        
        ligas_anuales = ["Primera-Division", "Primera-Nacional", "Serie-A-Brasil", "Copa-Libertadores", "Copa-Sudamericana", "Copa-Argentina", "Liga-MX", "MLS"]
        
        for liga_nombre, liga_id in LIGAS_CONFIG.items():
            if liga_nombre in ligas_anuales:
                temporadas_a_usar = TEMPORADAS_ANUALES
            else:
                temporadas_a_usar = TEMPORADAS_BIANUALES

            for temporada in temporadas_a_usar:
                datos = self.cosechar_temporada(liga_nombre, liga_id, temporada)
                if datos:
                    self.db.bulk_insert_historial(datos)
                
                pausa = random.uniform(8.0, 15.0)
                logging.info(f"Pausa de sigilo: durmiendo {pausa:.2f} segundos...")
                time.sleep(pausa)
                
        self.db.actualizar_cache_xg_global()
        logging.info("=== COSECHA MASIVA GLOBAL COMPLETADA ===")

def main():
    db = HarvesterDatabase()
    harvester = DataHarvester(db)
    harvester.ejecutar_cosecha_masiva()

if __name__ == "__main__":
    main()
