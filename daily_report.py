import sqlite3
import requests
import logging
import os
import math
import json
import unicodedata
from datetime import datetime, timezone
from typing import List, Tuple, Dict

# --- CONFIGURACIÓN ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
DB_PATH = "data/quant_master.db"
API_URL = f"https://api.telegram.org/bot{TOKEN}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

# --- CONFIGURACIÓN DE LIGAS (Agregá las que quieras) ---
SPORT_KEYS = [
    "soccer_argentina_primera_division",
    "soccer_conmebol_copa_libertadores",
    "soccer_spain_la_liga",
    "soccer_epl"
]

# --- MOTOR MATEMÁTICO (POISSON) ---
def poisson_pmf(k: int, lam: float) -> float:
    return (lam**k * math.exp(-lam)) / math.factorial(k)

def calcular_prediccion(xg_local: float, xg_visitante: float, max_goles: int = 5) -> Dict[str, float]:
    prob_local, prob_empate, prob_visitante = 0.0, 0.0, 0.0
    for g_loc in range(max_goles + 1):
        for g_vis in range(max_goles + 1):
            p = poisson_pmf(g_loc, xg_local) * poisson_pmf(g_vis, xg_visitante)
            if g_loc > g_vis: prob_local += p
            elif g_loc == g_vis: prob_empate += p
            else: prob_visitante += p
            
    total = prob_local + prob_empate + prob_visitante
    return {"local": prob_local / total, "empate": prob_empate / total, "visitante": prob_visitante / total}

def calcular_ev(prob: float, cuota: float) -> Tuple[float, bool]:
    ev = prob * cuota
    return ev, ev > 1.0

# --- NORMALIZADOR DE NOMBRES ---
def normalizar_texto(texto: str) -> str:
    texto = unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('utf-8')
    return texto.lower().strip().replace(" ", "")

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

# --- OBTENER PARTIDOS Y CUOTAS DE HOY DESDE THE ODDS API ---
def obtener_partidos_y_cuotas_hoy() -> List[Dict]:
    partidos_hoy = []
    hoy_utc = datetime.now(timezone.utc).date()
    
    if not ODDS_API_KEY:
        logging.warning("ODDS_API_KEY no configurada.")
        return []

    for sport in SPORT_KEYS:
        try:
            url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": "eu",
                "markets": "h2h",
                "oddsFormat": "decimal",
                "dateFormat": "iso"
            }
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                logging.error(f"Error API Odds ({sport}): {response.status_code}")
                continue
                
            eventos = response.json()
            for evento in eventos:
                # Filtrar por fecha (si se juega hoy)
                commence_time = evento.get("commence_time", "")
                if commence_time:
                    fecha_partido = datetime.fromisoformat(commence_time.replace("Z", "+00:00")).date()
                    if fecha_partido != hoy_utc:
                        continue # Si no es hoy, lo salteamos

                local_api = evento.get("home_team", "")
                visitante_api = evento.get("away_team", "")
                
                bookmakers = evento.get("bookmakers", [])
                if bookmakers:
                    mercados = bookmakers[0].get("markets", [])
                    for mercado in mercados:
                        if mercado.get("key") == "h2h":
                            cuotas = {"local": 0.0, "empate": 0.0, "visitante": 0.0}
                            for outcome in mercado.get("outcomes", []):
                                if outcome["name"] == local_api: cuotas["local"] = outcome["price"]
                                elif outcome["name"] == visitante_api: cuotas["visitante"] = outcome["price"]
                                elif outcome["name"] == "Draw": cuotas["empate"] = outcome["price"]
                            
                            if cuotas["local"] > 0 and cuotas["visitante"] > 0:
                                partidos_hoy.append({
                                    "local_api": local_api,
                                    "visitante_api": visitante_api,
                                    "cuotas": cuotas
                                })
        except Exception as e:
            logging.error(f"Excepción consultando Odds API ({sport}): {e}")
            
    return partidos_hoy

def enviar_telegram(mensaje: str):
    try:
        url = f"{API_URL}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"Error Telegram: {e}")

def main():
    logging.info("=== INICIANDO REPORTE MATUTINO PRO (API REAL CON FECHA) ===")
    fecha = datetime.now().strftime("%d/%m/%Y")
    
    partidos_api = obtener_partidos_y_cuotas_hoy()
    
    msg = f"☀️ <b>REPORTE MATUTINO PRO - {fecha}</b>\n"
    msg += "Análisis de Poisson + Cuotas Reales (Partidos de Hoy):\n\n"
    
    if not partidos_api:
        msg += "⚠️ <i>No se encontraron partidos programados para hoy en las ligas seleccionadas.</i>\n"
    else:
        for p in partidos_api:
            # Intentamos buscar los equipos de la API en nuestra base de datos
            xg_l, xga_l = obtener_xg(p["local_api"])
            xg_v, xga_v = obtener_xg(p["visitante_api"])
            
            xg_local_final = (xg_l + xga_v) / 2.0
            xg_visitante_final = (xg_v + xga_l) / 2.0
            
            probs = calcular_prediccion(xg_local_final, xg_visitante_final)
            cuotas = p["cuotas"]
            
            ev_l, val_l = calcular_ev(probs['local'], cuotas['local'])
            ev_e, val_e = calcular_ev(probs['empate'], cuotas['empate'])
            ev_v, val_v = calcular_ev(probs['visitante'], cuotas['visitante'])
            
            msg += f"🏆 <b>{p['local_api']} vs {p['visitante_api']}</b>\n"
            msg += f"📊 Prob: Local {probs['local']:.1%} | Empate {probs['empate']:.1%} | Visitante {probs['visitante']:.1%}\n"
            msg += f"💰 Cuotas: Local {cuotas['local']} | Empate {cuotas['empate']} | Visitante {cuotas['visitante']}\n"
            
            if val_l: msg += f"🚨 <b>VALUE BET LOCAL:</b> EV +{(ev_l-1)*100:.1f}%\n"
            if val_e: msg += f"🚨 <b>VALUE BET EMPATE:</b> EV +{(ev_e-1)*100:.1f}%\n"
            if val_v: msg += f"🚨 <b>VALUE BET VISITANTE:</b> EV +{(ev_v-1)*100:.1f}%\n"
            
            if not (val_l or val_e or val_v): msg += f"🛡️ Sin Value Bets (Mercado eficiente).\n"
            msg += "➖➖➖➖➖➖➖➖\n"
        
    msg += "\n🤖 <i>Generado por Terminator Motor Predictivo</i>"
    enviar_telegram(msg)
    logging.info("Reporte enviado.")

if __name__ == "__main__":
    main()
