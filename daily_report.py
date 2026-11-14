import os
import requests
import logging
from datetime import datetime

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
API_URL = f"https://api.telegram.org/bot{TOKEN}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

def enviar_telegram(mensaje: str):
    try:
        url = f"{API_URL}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"Error Telegram: {e}")

def main():
    logging.info("=== INICIANDO DIAGNÓSTICO DE API ===")
    
    if not ODDS_API_KEY:
        enviar_telegram("❌ DIAGNÓSTICO: La ODDS_API_KEY no está configurada en los secretos de GitHub.")
        return

    enviar_telegram(f"🔍 DIAGNÓSTICO: Clave detectada. Longitud: {len(ODDS_API_KEY)} caracteres.")
    
    # Probamos con la Liga Argentina, que sabemos que está activa
    sport = "soccer_argentina_primera_division"
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h"
    }
    
    try:
        response = requests.get(url, params=params, timeout=15)
        
        # Enviamos el código HTTP a Telegram
        enviar_telegram(f"📡 DIAGNÓSTICO: Código HTTP de la API: {response.status_code}")
        
        if response.status_code == 200:
            eventos = response.json()
            enviar_telegram(f"✅ DIAGNÓSTICO: La API devolvió {len(eventos)} partidos.")
            
            if len(eventos) > 0:
                # Si hay partidos, enviamos el primero para ver su estructura
                primer_partido = eventos[0]
                msg = f"🏆 <b>Partido crudo de la API:</b>\n"
                msg += f"Local: {primer_partido.get('home_team')}\n"
                msg += f"Visitante: {primer_partido.get('away_team')}\n"
                msg += f"Fecha: {primer_partido.get('commence_time')}\n"
                msg += f"Bookmakers disponibles: {len(primer_partido.get('bookmakers', []))}"
                enviar_telegram(msg)
            else:
                enviar_telegram("⚠️ DIAGNÓSTICO: La API respondió bien, pero devolvió 0 partidos para hoy.")
        else:
            # Si es un error, enviamos el mensaje de error crudo
            enviar_telegram(f"❌ DIAGNÓSTICO: Error de API: {response.text}")
            
    except Exception as e:
        enviar_telegram(f"❌ DIAGNÓSTICO: Excepción de red: {e}")

if __name__ == "__main__":
    main()
