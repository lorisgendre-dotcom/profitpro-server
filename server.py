#!/usr/bin/env python3
# server.py — ProfitPro HTTP Bridge (final)
#
# Rôles:
# - MT5 GET  /next_order     -> lit l'ordre courant (sans le vider)
# - MT5 POST /order_result   -> renvoie le résultat, puis on vide l'ordre
# - Bot  POST /push_order    -> pousse un ordre en mémoire
#
# Dans MT5: Outils > Options > Expert Advisors > Autoriser WebRequest pour:
#   http://127.0.0.1:5000

from flask import Flask, request, jsonify
import threading
import time
import logging
import requests  # pour Telegram

# ------------------------- Config -------------------------

DEFAULT_SYMBOL = "US30"
DEFAULT_LOT    = 0.1

TELEGRAM_ENABLED = True          # activé
SHEETS_ENABLED   = False

# Identifiants Telegram
TELEGRAM_TOKEN   = "7828242399:AAEDdiZQDGLwX4zEC_PTM2MYN1qmKwWVguo"
TELEGRAM_CHAT_ID = "-1002287155063"   # canal PROFITPRO

# ------------------------- Logging ------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("profitpro_server")

# --------------------- Stockage ordre ---------------------

_order_lock = threading.Lock()
_pending_order = None  # dict: id, direction, symbol, lot, sl, tp, ts

def _peek_order():
    with _order_lock:
        return dict(_pending_order) if _pending_order else None

def _set_order(order: dict):
    global _pending_order
    with _order_lock:
        _pending_order = dict(order) if order else None

def _clear_order():
    _set_order(None)

# ---------------------- Intégrations ----------------------

def send_telegram_message(text: str):
    """Envoie un message Telegram sur le canal PROFITPRO."""
    if not TELEGRAM_ENABLED:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": str(text),
            "parse_mode": "Markdown",
        }
        r = requests.post(url, json=payload, timeout=5)
        if not r.ok:
            log.error("Telegram HTTP error %s %s", r.status_code, r.text)
    except Exception as e:
        log.error("Telegram error: %s", e)

def append_trade_result_to_sheet(result: dict):
    pass  # placeholder inchangé

# ---------------------- API helpers -----------------------

def queue_order(direction: str,
                symbol: str = DEFAULT_SYMBOL,
                lot: float = DEFAULT_LOT,
                sl: float = 0.0,
                tp: float = 0.0) -> bool:
    """Place un ordre en mémoire. Écrase l'éventuel ordre existant."""
    if direction not in ("BUY", "SELL"):
        log.error("queue_order refusé: direction invalide %s", direction)
        return False
    order = {
        "id": str(int(time.time() * 1000)),
        "direction": direction,
        "symbol": str(symbol),
        "lot": float(lot),
        "sl": float(sl),
        "tp": float(tp),
        "ts": time.time(),
    }
    _set_order(order)
    log.info("Nouvel ordre en attente -> %s", order)

    # Notification Telegram sur nouvel ordre
    if TELEGRAM_ENABLED:
        try:
            txt = (
                "📥 *Nouvel ordre en attente*\n"
                f"• Direction : `{order['direction']}`\n"
                f"• Symbole : `{order['symbol']}`\n"
                f"• Lot : `{order['lot']}`\n"
                f"• SL : `{order['sl']}`\n"
                f"• TP : `{order['tp']}`\n"
                f"• ID interne : `{order['id']}`"
            )
            send_telegram_message(txt)
        except Exception as e:
            log.error("Telegram error: %s", e)

    return True

def handle_mt5_result(payload: dict):
    """
    Réception du résultat MT5.

    Cas prévus:
      - {"status":"OK","direction":"BUY","symbol":"US30","lot":1.0,"deal":"123","order":"456"}
      - {"status":"ERROR","reason":"..."}
      - {"event":"CLOSE","symbol":"US30","lot":1.0,"profit":123.45,"reason":"TP","deal":"123"}
    """
    log.info("Résultat MT5 -> %s", payload)

    # ---------- Telegram ----------
    if TELEGRAM_ENABLED:
        try:
            status = str(payload.get("status", "")).upper()
            event  = str(payload.get("event", "")).upper()

            direction = payload.get("direction")
            symbol    = payload.get("symbol")
            lot       = payload.get("lot")
            deal      = payload.get("deal")
            order_id  = payload.get("order")
            reason    = payload.get("reason")
            profit    = payload.get("profit")

            # Clôture (TP / SL / manuel...)
            if event == "CLOSE":
                txt = (
                    "📤 *Position clôturée*\n"
                    f"• Symbole : `{symbol}`\n"
                    f"• Lot : `{lot}`\n"
                    f"• Profit : `{profit}`\n"
                    f"• Raison : `{reason}`\n"
                    f"• Deal : `{deal}`"
                )

            # Exécution OK
            elif status == "OK":
                txt = (
                    "✅ *Ordre exécuté*\n"
                    f"• Direction : `{direction}`\n"
                    f"• Symbole : `{symbol}`\n"
                    f"• Lot : `{lot}`\n"
                    f"• Deal : `{deal}`\n"
                    f"• Order : `{order_id}`"
                )

            # Erreur
            elif status == "ERROR":
                txt = (
                    "❌ *Erreur exécution MT5*\n"
                    f"• Raison : `{reason}`\n"
                    f"• Payload : `{payload}`"
                )

            # Fallback générique
            else:
                txt = f"ℹ️ *MT5 event brut* : `{payload}`"

            send_telegram_message(txt)

        except Exception as e:
            log.error("Telegram error: %s", e)

    # ---------- Google Sheets éventuel ----------
    if SHEETS_ENABLED:
        try:
            append_trade_result_to_sheet(payload)
        except Exception as e:
            log.error("Sheets error: %s", e)

# ------------------------- Flask --------------------------

app = Flask(__name__)

@app.route("/next_order", methods=["GET"])
def next_order():
    """MT5 lit l'ordre courant. Ne le vide pas."""
    try:
        order = _peek_order()
        if order is None:
            log.info("next_order -> EMPTY")
            return "EMPTY", 200
        payload = {
            "id":        order.get("id", ""),
            "direction": order.get("direction", ""),
            "symbol":    order.get("symbol", ""),
            "lot":       float(order.get("lot", 0.0)),
            "sl":        float(order.get("sl", 0.0)),
            "tp":        float(order.get("tp", 0.0)),
        }
        log.info("next_order -> SEND %s", payload)
        return jsonify(payload), 200
    except Exception as e:
        log.exception("next_order crashed: %s", e)
        # Toujours renvoyer du JSON pour éviter <html> côté MT5
        return jsonify({"error": "server_exception", "detail": str(e)}), 200

@app.route("/order_result", methods=["POST"])
def order_result():
    """MT5 renvoie le résultat. On vide l'ordre après ACK."""
    try:
        data = request.get_json(force=True, silent=False)
        if not isinstance(data, dict):
            log.error("order_result: payload non dict: %s", data)
            return "BAD_FORMAT", 400
        handle_mt5_result(data)
        _clear_order()
        return "ACK", 200
    except Exception as e:
        log.exception("order_result crashed: %s", e)
        # On ACK quand même pour éviter les retries MT5
        return "ACK", 200

@app.route("/push_order", methods=["POST"])
def push_order():
    """Bot ou humain: push d'un ordre."""
    try:
        data = request.get_json(force=True, silent=False)
        direction = data.get("direction")
        symbol    = data.get("symbol", DEFAULT_SYMBOL)
        lot       = float(data.get("lot", DEFAULT_LOT))
        sl        = float(data.get("sl", 0.0))
        tp        = float(data.get("tp", 0.0))
        ok = queue_order(direction=direction, symbol=symbol, lot=lot, sl=sl, tp=tp)
        if not ok:
            return "BAD_DIRECTION", 400
        return "QUEUED", 200
    except Exception as e:
        log.exception("push_order crashed: %s", e)
        return "BAD_JSON", 400

# ------------------------- Main ---------------------------

if __name__ == "__main__":
    log.info("ProfitPro HTTP Server démarré sur http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)