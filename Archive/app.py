from flask import Flask, request, jsonify
from pydantic import BaseModel, Field, ValidationError
from typing import Optional
import time

from utils.logger import get_logger
from config import settings
from utils.telegram import send_message
from utils.sheets import append_row
from utils.zmq_bridge import ZmqClient
from strategies.harmonic import HarmonicSignal, compute_sl_tp, basic_confirmations

app = Flask(__name__)
log = get_logger("app", settings.LOG_LEVEL)
zmq_client = ZmqClient()

class TvAlert(BaseModel):
    symbol: str = Field(default=settings.DEFAULT_SYMBOL)
    pattern: str
    side: str            # BUY / SELL
    price: float
    prz_low: Optional[float] = None
    prz_high: Optional[float] = None
    rsi: Optional[float] = None
    supertrend: Optional[str] = None
    risk_reward: Optional[str] = "1:2"
    magic: Optional[int] = 88001
    comment: Optional[str] = "ProfitPro"
    lot: Optional[float] = settings.DEFAULT_LOT

def _check_secret(req) -> bool:
    secret = req.headers.get("X-Webhook-Secret", "")
    return secret and secret == settings.WEBHOOK_SECRET

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "Profit Pro"}), 200

@app.route("/tv-webhook", methods=["POST"])
def tv_webhook():
    # 1) Sécurité
    if not _check_secret(request):
        log.warning("Webhook secret invalide.")
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    # 2) Parsing / validation
    try:
        payload = request.get_json(force=True)
        data = TvAlert(**payload)
    except ValidationError as e:
        log.error("Payload invalide: %s", e)
        return jsonify({"ok": False, "error": "bad_payload"}), 400
    except Exception as e:
        log.exception("Erreur lecture payload: %s", e)
        return jsonify({"ok": False, "error": "server_error"}), 500

    # 3) Normalisation signal
    sig = HarmonicSignal(
        symbol=data.symbol,
        pattern=data.pattern,
        side=data.side.upper(),
        price=float(data.price),
        prz_low=data.prz_low,
        prz_high=data.prz_high,
        rsi=data.rsi,
        supertrend=(data.supertrend.lower() if data.supertrend else None),
        risk_reward=data.risk_reward
    )

    # 4) Confirmations minimales
    if not basic_confirmations(sig):
        msg = f"❌ <b>Signal rejeté</b> ({sig.pattern} {sig.side} {sig.symbol}) — confirmations KO."
        log.info(msg)
        send_message(msg)
        append_row([
            time.strftime("%Y-%m-%d %H:%M:%S"),
            sig.symbol, sig.pattern, sig.side, sig.price,
            sig.prz_low or "", sig.prz_high or "",
            sig.rsi or "", sig.supertrend or "",
            "REJECTED", "", "", "", "Confirmations KO"
        ])
        return jsonify({"ok": True, "status": "rejected"}), 200

    # 5) SL/TP
    sl, tp = compute_sl_tp(sig)

    # 6) Envoi ordre à l’EA (ZMQ)
    order = {
        "type": "ORDER",
        "symbol": sig.symbol,
        "side": sig.side,
        "lot": float(data.lot or settings.DEFAULT_LOT),
        "price": sig.price,           # l’EA peut traiter market si vide; ici on met la price
        "sl": sl,
        "tp": tp,
        "slippage": settings.SLIPPAGE_POINTS,
        "magic": data.magic or 88001,
        "comment": data.comment or "ProfitPro"
    }
    zmq_client.send_order(order)

    # 7) Telegram
    tmsg = (
        f"🚀 <b>Entrée envoyée</b>\n"
        f"• {sig.symbol} {sig.side}\n"
        f"• Pattern: {sig.pattern}\n"
        f"• Prix: {sig.price}\n"
        f"• SL: {sl} | TP: {tp}\n"
        f"• RSI: {sig.rsi} | ST: {sig.supertrend}\n"
        f"• RR: {sig.risk_reward}\n"
        f"• Lot: {order['lot']}"
    )
    send_message(tmsg)

    # 8) Journal Google Sheet
    append_row([
        time.strftime("%Y-%m-%d %H:%M:%S"),
        sig.symbol, sig.pattern, sig.side, sig.price,
        sig.prz_low or "", sig.prz_high or "",
        sig.rsi or "", sig.supertrend or "",
        "SENT", sl, tp, order["lot"], ""
    ])

    return jsonify({"ok": True, "status": "sent", "order": order}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)