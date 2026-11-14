import sqlite3
import requests
import logging
import os
import math
import json
import re
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Dict

# --- CONFIGURACIÓN ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
DB_PATH = "data/quant_master.db"
API_URL = f"https://api.telegram.org/bot{TOKEN}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

# --- LIGAS OPTIMIZADAS (Las 5 Grandes) ---
SPORT_KEYS = [
    "soccer_argentina_primera_division", # Argentina
    "soccer_epl",                        # Inglaterra (Premier League)
    "soccer_spain_la_liga",              # España
    "soccer_italy_serie_a",              # Italia
    "soccer_germany_bundesliga"          # Alemania
]

# --- MOTOR MATEMÁTICO (POISSON) ---
def poisson_pmf(k: int, lam: float) -> float:
    return (lam**k * math.exp(-lam)) / math.factorial(k)

def calcular_prediccion(xg_local: float, xg_visitante: float, max_goles: int = 6) -> Dict[str, float]:
    prob_local, prob_empate, prob_visitante = 0.0, 0.0, 0.0
    prob_over_25 = 0.0
    
    for g_loc in range(max_goles + 1):
        for g_vis in range(max_goles + 1):
            p = poisson_pmf(g_loc, xg_local) * poisson_pmf(g_vis, xg_visitante)
            if g_loc > g_vis: prob_local += p
            elif g_loc == g_vis: prob_empate += p
            else: prob_visitante += p
            
            if (g_loc + g_vis) > 2.5:
                prob_over_25 += p
                
    total = prob_local + prob_empate + prob_visitante
    return {
        "local": prob_local / total, 
        "empate": prob_empate / total, 
        "visitante": prob_visitante / total,
        "over_25": prob_over_25 / total,
        "under_25": 1.0 - (prob_over_25 / total),
        "1X": (prob_local + prob_empate) / total,
        "X2": (prob_empate + prob_visitante) / total,
        "12": (prob_local + prob_visitante) / total
    }

def calcular_ev(prob: float, cuota: float) -> Tuple[float, bool]:
    ev = prob * cuota
    return ev, ev > 1.05 # Umbral de Value Bet: 5% de ventaja

# --- LIMPIADOR DE NOMBRES ---
def limpiar_nombre_equipo(nombre: str) -> str:
    nombre = nombre.lower()
    palabras_basura = ["ca ", "cf ", "fc ", "club atletico ", "ba", "de cordoba", "de la plata", "sarsfield"]
    for palabra in palabras_basura:
        nombre = nombre.replace(palabra, "")
    return nombre.strip()

# --- BASE DE DATOS ---
def obtener_xg(equipo: str) -> Tuple[float, float, bool]:
    equipo_limpio = limpiar_nombre_equipo(equipo)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                "SELECT xg, xga FROM cache_xg WHERE equipo LIKE ? LIMIT 1", 
                (f"%{equipo_limpio}%",)
            )
            row = cursor.fetchone()
            if row: return row[0], row[1], True
    except Exception as e:
        logging.error(f"Error BD: {e}")
    return 1.3, 1.1, False

# --- OBTENER PARTIDOS Y CUOTAS (CON AHORRO DE CRÉDITOS) ---
def obtener_partidos_y_cuotas() -> List[Dict]:
    partidos = []
    ahora = datetime.now(timezone.utc)
    limite_futuro = ahora + timedelta(days=7)

    if not ODDS_API_KEY:
        return []

    for sport in SPORT_KEYS:
        try:
            url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
            # 2 mercados (h2h + totals) y 1 región (eu) = 2 créditos por liga
            params = {"apiKey": ODDS_API_KEY, "regions": "eu", "markets": "h2h,totals", "oddsFormat": "decimal"}
            response = requests.get(url, params=params, timeout=10)
            
            # Logueamos los créditos restantes para control
            creditos_restantes = response.headers.get("x-requests-remaining", "Desconocido")
            logging.info(f"Liga {sport} consultada. Créditos restantes: {creditos_restantes}")
            
            if response.status_code != 200: continue
                
            for evento in response.json():
                fecha_str = evento.get("commence_time", "")
                if fecha_str:
                    fecha_partido = datetime.fromisoformat(fecha_str.replace("Z", "+00:00"))
                    if fecha_partido > limite_futuro: continue

                local_api = evento.get("home_team", "")
                visitante_api = evento.get("away_team", "")
                
                cuotas = {"local": 0.0, "empate": 0.0, "visitante": 0.0, "over": 0.0, "under": 0.0}
                bookmakers = evento.get("bookmakers", [])
                if bookmakers:
                    for mercado in bookmakers[0].get("markets", []):
                        # Mercado 1X2
                        if mercado.get("key") == "h2h":
                            for outcome in mercado.get("outcomes", []):
                                if outcome["name"] == local_api: cuotas["local"] = outcome["price"]
                                elif outcome["name"] == visitante_api: cuotas["visitante"] = outcome["price"]
                                elif outcome["name"] == "Draw": cuotas["empate"] = outcome["price"]
                        # Mercado Over/Under
                        elif mercado.get("key") == "totals":
                            for outcome in mercado.get("outcomes", []):
                                if outcome["name"] == "Over" and outcome.get("point") == 2.5: cuotas["over"] = outcome["price"]
                                elif outcome["name"] == "Under" and outcome.get("point") == 2.5: cuotas["under"] = outcome["price"]
                            
                if cuotas["local"] > 0 and cuotas["visitante"] > 0:
                    partidos.append({"local": local_api, "visitante": visitante_api, "cuotas": cuotas, "fecha": fecha_str})
        except Exception as e:
            logging.error(f"Error API ({sport}): {e}")
            
    partidos.sort(key=lambda x: x["fecha"])
    return partidos

def enviar_telegram(mensaje: str):
    try:
        requests.post(f"{API_URL}/sendMessage", json={"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        logging.error(f"Error Telegram: {e}")

def main():
    fecha = datetime.now().strftime("%d/%m/%Y")
    partidos_api = obtener_partidos_y_cuotas()
    
    msg = f"☀️ <b>REPORTE MATUTINO PRO - {fecha}</b>\n"
    msg += "<i>Análisis de Poisson + Cuotas Reales</i>\n\n"
    
    if not partidos_api:
        msg += "⚠️ <i>No hay partidos disponibles en las 5 Grandes Ligas en este momento.</i>\n"
    else:
        partidos_mostrados = 0
        for p in partidos_api:
            if partidos_mostrados >= 5: 
                break
                
            xg_l, xga_l, found_l = obtener_xg(p["local"])
            xg_v, xga_v, found_v = obtener_xg(p["visitante"])
            
            # FILTRO ANTI-FALSOS POSITIVOS
            if not found_l or not found_v:
                logging.warning(f"Saltando {p['local']} vs {p['visitante']} por falta de datos en DB.")
                continue

            xg_local_final = (xg_l + xga_v) / 2.0
            xg_visitante_final = (xg_v + xga_l) / 2.0
            
            probs = calcular_prediccion(xg_local_final, xg_visitante_final)
            cuotas = p["cuotas"]
            
            fecha_partido = datetime.fromisoformat(p["fecha"].replace("Z", "+00:00")) - timedelta(hours=3)
            fecha_formateada = fecha_partido.strftime("%d/%m %H:%M hs")
            
            msg += f"🏆 <b>{p['local']} vs {p['visitante']}</b>\n"
            msg += f"📅 {fecha_formateada}\n\n"
            
            msg += f"📊 <b>1X2:</b> Local {probs['local']:.1%} | Empate {probs['empate']:.1%} | Visita {probs['visitante']:.1%}\n"
            msg += f"🛡️ <b>Doble Oportunidad:</b> 1X {probs['1X']:.1%} | X2 {probs['X2']:.1%} | 12 {probs['12']:.1%}\n"
            msg += f"⚽ <b>Goles (O/U 2.5):</b> Over {probs['over_25']:.1%} | Under {probs['under_25']:.1%}\n\n"
            
            msg += f"💰 <b>Cuotas 1X2:</b> {cuotas['local']} | {cuotas['empate']} | {cuotas['visitante']}\n"
            if cuotas['over'] > 0:
                msg += f"💰 <b>Cuotas Goles:</b> Over {cuotas['over']} | Under {cuotas['under']}\n"
            
            ev_l, val_l = calcular_ev(probs['local'], cuotas['local'])
            ev_e, val_e = calcular_ev(probs['empate'], cuotas['empate'])
            ev_v, val_v = calcular_ev(probs['visitante'], cuotas['visitante'])
            
            if val_l: msg += f"🚨 VALUE BET LOCAL: EV +{(ev_l-1)*100:.1f}%\n"
            if val_e: msg += f"🚨 VALUE BET EMPATE: EV +{(ev_e-1)*100:.1f}%\n"
            if val_v: msg += f"🚨 VALUE BET VISITANTE: EV +{(ev_v-1)*100:.1f}%\n"
            
            if cuotas['over'] > 0:
                ev_over, val_over = calcular_ev(probs['over_25'], cuotas['over'])
                ev_under, val_under = calcular_ev(probs['under_25'], cuotas['under'])
                if val_over: msg += f"🚨 VALUE BET OVER 2.5: EV +{(ev_over-1)*100:.1f}%\n"
                if val_under: msg += f"🚨 VALUE BET UNDER 2.5: EV +{(ev_under-1)*100:.1f}%\n"
            
            if not (val_l or val_e or val_v or (cuotas['over'] > 0 and (val_over or val_under))):
                msg += f"🛡️ Sin Value Bets.\n"
                
            msg += "➖➖➖➖➖➖➖➖\n"
            partidos_mostrados += 1
        
    if partidos_mostrados == 0:
        msg += "🛡️ <i>Todos los partidos fueron filtrados por falta de datos en la DB.</i>\n"
        
    msg += "\n🤖 <i>Generado por Terminator Motor Predictivo</i>"
    enviar_telegram(msg)

if __name__ == "__main__":
    main()
