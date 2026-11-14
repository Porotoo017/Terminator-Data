import sqlite3
import requests
import logging
import os
import math
import json
from datetime import datetime
from typing import List, Tuple, Dict
from playwright.sync_api import sync_playwright

# --- CONFIGURACIÓN ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DB_PATH = "data/quant_master.db"
API_URL = f"https://api.telegram.org/bot{TOKEN}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

# --- DICCIONARIO DE CUOTAS REALES (Ingresá acá las cuotas que veas en tu casa de apuestas antes de las 8 AM) ---
# Formato: "nombre_equipo_local_nombre_equipo_visitante": {"local": X, "empate": Y, "visitante": Z}
CUOTAS_MANUALES = {
    # Ejemplo (los nombres deben coincidir con los de Promiedos):
    # "boca juniors_river plate": {"local": 2.50, "empate": 3.10, "visitante": 2.80}
}

# --- MOTOR MATEMÁTICO ---
def poisson_pmf(k: int, lam: float) -> float:
    return (lam**k * math.exp(-lam)) / math.factorial(k)

def calcular_prediccion(xg_local: float, xg_visitante: float, max_goles: int = 5) -> Dict[str, float]:
    prob_local = 0.0
    prob_empate = 0.0
    prob_visitante = 0.0
    
    for g_loc in range(max_goles + 1):
        for g_vis in range(max_goles + 1):
            p = poisson_pmf(g_loc, xg_local) * poisson_pmf(g_vis, xg_visitante)
            if g_loc > g_vis: prob_local += p
            elif g_loc == g_vis: prob_empate += p
            else: prob_visitante += p
            
    total = prob_local + prob_empate + prob_visitante
    return {
        "local": prob_local / total,
        "empate": prob_empate / total,
        "visitante": prob_visitante / total
    }

def calcular_ev(prob: float, cuota: float) -> Tuple[float, bool]:
    ev = prob * cuota
    return ev, ev > 1.0

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
            page.goto("https://www.promiedos.com.ar/", timeout=30000)
            page.wait_for_timeout(5000) 
            content = page.inner_text("body")
            browser.close()
            
            equipos_conocidos = ["boca", "river", "racing", "independiente", "san lorenzo", "huracan", "velez", "estudiantes", "gimnasia", "real madrid", "barcelona", "manchester city", "arsenal", "psg", "bayern", "inter", "milan"]
            lineas = content.split("\n")
            equipos_en_pagina = []
            
            for linea in lineas:
                linea_lower = linea.lower().strip()
                if any(eq in linea_lower for eq in equipos_conocidos) and len(linea_lower) < 20:
                    equipos_en_pagina.append(linea_lower)
            
            if len(equipos_en_pagina) >= 2:
                for i in range(0, len(equipos_en_pagina) - 1, 2):
                    if equipos_en_pagina[i] != equipos_en_pagina[i+1]:
                        partidos.append((equipos_en_pagina[i], equipos_en_pagina[i+1]))
                        
            if not partidos:
                partidos = [("boca juniors", "river plate")]
    except Exception as e:
        logging.error(f"Error scraper: {e}")
        partidos = [("boca juniors", "river plate")]
    return partidos

def enviar_telegram(mensaje: str):
    try:
        url = f"{API_URL}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"Error Telegram: {e}")

def main():
    logging.info("=== INICIANDO REPORTE MATUTINO PRO CON VALUE BET ===")
    fecha = datetime.now().strftime("%d/%m/%Y")
    partidos = obtener_partidos_hoy()
    
    msg = f"☀️ <b>REPORTE MATUTINO PRO - {fecha}</b>\n"
    msg += "Análisis de Poisson y Value Bet:\n\n"
    
    for local, visitante in partidos:
        xg_l, xga_l = obtener_xg(local)
        xg_v, xga_v = obtener_xg(visitante)
        
        xg_local_final = (xg_l + xga_v) / 2.0
        xg_visitante_final = (xg_v + xga_l) / 2.0
        
        probs = calcular_prediccion(xg_local_final, xg_visitante_final)
        
        msg += f"🏆 <b>{local.title()} vs {visitante.title()}</b>\n"
        msg += f"📊 Probabilidad Local: {probs['local']:.1%} | Empate: {probs['empate']:.1%} | Visitante: {probs['visitante']:.1%}\n"
        
        # --- VERIFICACIÓN DE CUOTAS REALES ---
        clave_partido = f"{local}_{visitante}"
        if clave_partido in CUOTAS_MANUALES:
            cuotas = CUOTAS_MANUALES[clave_partido]
            ev_l, val_l = calcular_ev(probs['local'], cuotas['local'])
            ev_e, val_e = calcular_ev(probs['empate'], cuotas['empate'])
            ev_v, val_v = calcular_ev(probs['visitante'], cuotas['visitante'])
            
            msg += f"💰 Cuotas ingresadas: Local {cuotas['local']} | Empate {cuotas['empate']} | Visitante {cuotas['visitante']}\n"
            
            if val_l: msg += f"🚨 <b>VALUE BET LOCAL:</b> EV +{(ev_l-1)*100:.1f}%\n"
            if val_e: msg += f"🚨 <b>VALUE BET EMPATE:</b> EV +{(ev_e-1)*100:.1f}%\n"
            if val_v: msg += f"🚨 <b>VALUE BET VISITANTE:</b> EV +{(ev_v-1)*100:.1f}%\n"
            
            if not (val_l or val_e or val_v): msg += f"🛡️ Sin Value Bets (Mercado eficiente).\n"
        else:
            msg += f"⚠️ <i>Faltan cargar cuotas reales para este partido en el código.</i>\n"
            
        msg += "➖➖➖➖➖➖➖➖\n"
        
    enviar_telegram(msg)
    logging.info("Reporte enviado.")

if __name__ == "__main__":
    main()
