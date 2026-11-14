import os
import requests
import sqlite3
import json
import logging
import time
from typing import Dict, Any

# --- CONFIGURACIÓN ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_PATH = "data/quant_master.db"
API_URL = f"https://api.telegram.org/bot{TOKEN}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

def enviar_mensaje(chat_id: int, texto: str, reply_markup: Dict = None):
    payload = {"chat_id": chat_id, "text": texto, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        requests.post(f"{API_URL}/sendMessage", json=payload, timeout=10)
    except Exception as e:
        logging.error(f"Error enviando mensaje: {e}")

def obtener_estadisticas_equipo(nombre_equipo: str) -> str:
    if not os.path.exists(DB_PATH):
        return "⚠️ La base de datos no está lista todavía."
        
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                "SELECT equipo, xg, xga FROM cache_xg WHERE equipo LIKE ? LIMIT 1", 
                (f"%{nombre_equipo.lower()}%",)
            )
            row = cursor.fetchone()
            if row:
                equipo, xg, xga = row
                return (f"📊 <b>Estadísticas Históricas: {equipo.title()}</b>\n\n"
                        f"⚽ xG (Goles Esperados a favor): <b>{xg}</b>\n"
                        f"🛡️ xGA (Goles Esperados en contra): <b>{xga}</b>\n\n"
                        f"<i>Datos extraídos del Harvester Global</i>")
            else:
                return f"❌ No encontré datos para '<b>{nombre_equipo}</b>'. Verificá el nombre o intentá con otro equipo."
    except Exception as e:
        return f"⚠️ Error consultando la base de datos: {e}"

def procesar_mensaje(mensaje: Dict[str, Any]):
    chat_id = mensaje["chat"]["id"]
    texto = mensaje.get("text", "")
    
    if texto.startswith("/start"):
        teclado = {
            "inline_keyboard": [
                [{"text": "📈 Buscar Equipo (Ej: /stats boca)", "callback_data": "ayuda"}]
            ]
        }
        enviar_mensaje(chat_id, "🤖 <b>Fútbol Data V1</b>\n\nSoy tu analista de datos deportivos. Escribí el comando <code>/stats [equipo]</code> para ver sus probabilidades históricas.\n\nEjemplo: <code>/stats river</code>", reply_markup=teclado)
        
    elif texto.startswith("/stats"):
        partes = texto.split(maxsplit=1)
        if len(partes) > 1:
            equipo = partes[1]
            respuesta = obtener_estadisticas_equipo(equipo)
            enviar_mensaje(chat_id, respuesta)
        else:
            enviar_mensaje(chat_id, "Usá el formato: <code>/stats [nombre equipo]</code>. Ejemplo: /stats river")

def main():
    try:
        # PARCHE: Eliminar cualquier Webhook que esté bloqueando la lectura de mensajes
        requests.get(f"{API_URL}/deleteWebhook", timeout=10)
        
        response = requests.get(f"{API_URL}/getUpdates?timeout=5", timeout=10)
        if response.status_code != 200:
            logging.error(f"Error de API: {response.text}")
            return
            
        updates = response.json().get("result", [])
        
        for update in updates:
            if "message" in update:
                mensaje = update["message"]
                
                if time.time() - mensaje["date"] > 3600:
                    continue
                    
                logging.info(f"Mensaje recibido: {mensaje.get('text', '')}")
                procesar_mensaje(mensaje)
                
                update_id = update["update_id"]
                requests.get(f"{API_URL}/getUpdates?offset={update_id + 1}", timeout=5)
    except Exception as e:
        logging.error(f"Error en el bucle principal: {e}")

if __name__ == "__main__":
    main()
