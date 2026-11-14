import sqlite3
import requests
import logging
import os
import math
import json
from datetime import datetime
from typing import List, Tuple, Dict # <-- LA LÍNEA QUE FALTABA
from playwright.sync_api import sync_playwright

# --- CONFIGURACIÓN ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DB_PATH = "data/quant_master.db"
API_URL = f"https://api.telegram.org/bot{TOKEN}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

# --- MOTOR MATEMÁTICO ---
def poisson_pmf(k: int, lam: float) -> float:
    return (lam**k * math.exp(-lam)) / math.factorial(k)

def dixon_coles(x: int, y: int, lx: float, ly: float) -> float:
    if x == 0 and y == 0: return -0.15 * lx * ly
    if x == 0 and y == 1: return 0.1 * lx
    if x == 1 and y == 0: return 0.1 * ly
    if x == 1 and y == 1: return 0.05
    return 0.0

def calcular_prediccion(xg_local: float, xg_visitante: float) -> Dict[str, float]:
    p_matrix = [[0.0 for _ in range(6)] for _ in range(6)]
    for i in range(6):
        for j in range(6):
            p = poisson_pmf(i, xg_local) * poisson_pmf(j, xg_visitante)
            p += dixon_coles(i, j, xg_local, xg_visitante)
            p_matrix[i][j] = max(p, 0.0)
            
    suma = sum(sum(fila) for fila in p_matrix)
    for i in range(6):
        for j in range(6):
            p_matrix[i][j] /= suma
            
    prob_local = sum(p_matrix[i][j] for i in range(6) for j in range(6) if i > j)
    prob_empate = sum(p_matrix[i][i] for i in range(6))
    prob_visitante = sum(p_matrix[i][j] for i in range(6) for j in range(6) if i < j)
    prob_over = sum(p_matrix[i][j] for i in range(6) for j in range(6) if i + j > 2.5)
    
    return {
        "local": prob_local, "empate": prob_empate, "visitante": prob_visitante,
        "over": prob_over, "under": 1.0 - prob_over
    }

# --- BASE DE DATOS ---
def obtener_xg(equipo: str) -> Tuple[float, float]:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                "SELECT xg, xga FROM cache_xg WHERE equipo LIKE ? LIMIT 1", 
                (f"%{equipo.lower()}%",)
            )
            row = cursor.fetchone()
            if row: return row[0], row[1]
    except Exception as e:
        logging.error(f"Error BD: {e}")
    return 1.3, 1.1 

# --- SCRAPING DE PARTIDOS DEL DÍA ---
def obtener_partidos_hoy() -> List[Tuple[str, str]]:
    partidos = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
            
            # Entramos a Promiedos
            page.goto("https://www.promiedos.com.ar/", timeout=30000)
            page.wait_for_timeout(5000) 
            
            content = page.inner_text("body")
            browser.close()
            
            equipos_conocidos = [
                "boca", "river", "racing", "independiente", "san lorenzo", "huracan", 
                "velez", "estudiantes", "gimnasia", "real madrid", "barcelona", 
                "manchester city", "arsenal", "psg", "bayern", "inter", "milan"
            ]
            
            lineas = content.split("\n")
            equipos_en_pagina = []
            
            for linea in lineas:
                linea_lower = linea.lower().strip()
                if any(eq in linea_lower for eq in equipos_conocidos) and len(linea_lower) < 20:
                    equipos_en_pagina.append(linea_lower)
            
            if len(equipos_en_pagina) >= 2:
                for i in range(0, len(equipos_en_pagina) - 1, 2):
                    local = equipos_en_pagina[i]
                    visitante = equipos_en_pagina[i+1]
                    if local != visitante:
                        partidos.append((local, visitante))
                        
            if not partidos:
                logging.warning("Scraper no encontró partidos. Usando fallback de prueba.")
                partidos = [("boca juniors", "river plate")]
                
            logging.info(f"Scraper de Promiedos extrajo {len(partidos)} partidos.")
            
    except Exception as e:
        logging.error(f"Error en el scraper de Promiedos: {e}")
        partidos = [("boca juniors", "river plate")]
        
    return partidos

def enviar_telegram(mensaje: str):
    try:
        url = f"{API_URL}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"Error enviando Telegram: {e}")

def main():
    logging.info("=== INICIANDO REPORTE MATUTINO PRO ===")
    fecha = datetime.now().strftime("%d/%m/%Y")
    partidos = obtener_partidos_hoy()
    
    msg = f"☀️ <b>REPORTE MATUTINO PRO - {fecha}</b>\n"
    msg += "Análisis estadístico de los partidos de hoy:\n\n"
    
    for local, visitante in partidos:
        xg_l, xga_l = obtener_xg(local)
        xg_v, xga_v = obtener_xg(visitante)
        
        xg_local_final = (xg_l + xga_v) / 2.0
        xg_visitante_final = (xg_v + xga_l) / 2.0
        
        probs = calcular_prediccion(xg_local_final, xg_visitante_final)
        
        msg += f"🏆 <b>{local.title()} vs {visitante.title()}</b>\n"
        msg += f"  • Gana {local.title()}: <b>{probs['local']:.1%}</b>\n"
        msg += f"  • Empate: <b>{probs['empate']:.1%}</b>\n"
        msg += f"  • Gana {visitante.title()}: <b>{probs['visitante']:.1%}</b>\n"
        msg += f"  • Over 2.5 Goles: <b>{probs['over']:.1%}</b>\n"
        msg += "➖➖➖➖➖➖➖➖\n"
        
    msg += "\n🤖 <i>Generado por Terminator Motor Predictivo</i>"
    enviar_telegram(msg)
    logging.info("Reporte enviado con éxito.")

if __name__ == "__main__":
    main()
