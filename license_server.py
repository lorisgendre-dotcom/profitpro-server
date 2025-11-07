#!/usr/bin/env python3
# license_server.py — gestion des licences ProfitPro (avec expiration)

import os
import sqlite3
import secrets
import time
import logging
import json

from flask import Flask, request, jsonify
from dotenv import load_dotenv
import stripe

DB_PATH = "licenses.db"

# --------- Stripe + .env ---------
load_dotenv()

STRIPE_SECRET_KEY     = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID       = os.getenv("STRIPE_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

STRIPE_SUCCESS_URL    = os.getenv("STRIPE_SUCCESS_URL", "https://google.com/success")
STRIPE_CANCEL_URL     = os.getenv("STRIPE_CANCEL_URL", "https://google.com/cancel")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY
# ---------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("license_server")

app = Flask(__name__)

# ---------------------- DB helpers ----------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS licenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key TEXT UNIQUE NOT NULL,
            email TEXT,
            mt5_account TEXT,
            status TEXT NOT NULL,
            expires_at INTEGER,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()
    log.info("DB initialisée (%s)", DB_PATH)


def create_license(email: str, days_valid: int = 30) -> str:
    """Crée une licence 'active' avec expiration dans N jours."""
    key = secrets.token_urlsafe(16)
    now = int(time.time())
    expires_at = now + days_valid * 86400
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO licenses (license_key, email, mt5_account, status, expires_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (key, email, "", "active", expires_at, now, now),
    )
    conn.commit()
    conn.close()
    log.info("Licence créée pour %s -> %s (expire le %s)", email, key, time.ctime(expires_at))
    return key


def find_license(license_key: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM licenses WHERE license_key = ?", (license_key,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def set_license_status(license_key: str, status: str):
    now = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE licenses SET status = ?, updated_at = ? WHERE license_key = ?",
        (status, now, license_key),
    )
    conn.commit()
    conn.close()
    log.info("Licence %s -> status=%s", license_key, status)


def set_license_expiry(license_key: str, expires_at: int):
    now = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE licenses SET expires_at = ?, updated_at = ? WHERE license_key = ?",
        (expires_at, now, license_key),
    )
    conn.commit()
    conn.close()
    log.info("Licence %s -> expires_at=%s", license_key, time.ctime(expires_at))


def bind_account_if_needed(license_key: str, account: str):
    """Si mt5_account est vide, on enregistre le premier compte qui se connecte."""
    now = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM licenses WHERE license_key = ?", (license_key,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    lic = dict(row)
    if not lic["mt5_account"]:
        cur.execute(
            "UPDATE licenses SET mt5_account = ?, updated_at = ? WHERE license_key = ?",
            (account, now, license_key),
        )
        conn.commit()
        log.info("Licence %s liée au compte MT5 %s", license_key, account)
        lic["mt5_account"] = account
    conn.close()
    return lic

# ---------------------- API routes admin / health ----------------------

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "OK"}), 200


@app.route("/api/admin/create_license", methods=["POST"])
def api_admin_create_license():
    """
    Création manuelle d'une licence.
    Body JSON: { "email": "client@exemple.com", "days": 30 }
    """
    data = request.get_json(force=True, silent=False)
    email = data.get("email", "").strip()
    days  = int(data.get("days", 30))
    if not email:
        return jsonify({"error": "missing_email"}), 400

    key = create_license(email, days_valid=days)
    return jsonify({"license_key": key, "email": email, "days": days}), 200


@app.route("/api/admin/deactivate", methods=["POST"])
def api_admin_deactivate():
    """
    Désactiver une licence (après non-paiement par ex).
    Body JSON: { "license_key": "..." }
    """
    data = request.get_json(force=True, silent=False)
    license_key = str(data.get("license_key", "")).strip()
    if not license_key:
        return jsonify({"error": "missing_license_key"}), 400

    lic = find_license(license_key)
    if not lic:
        return jsonify({"error": "unknown_license"}), 200

    set_license_status(license_key, "inactive")
    return jsonify({"status": "OK"}), 200


@app.route("/api/admin/set_expiry", methods=["POST"])
def api_admin_set_expiry():
    """
    Modifier la date d'expiration (Unix timestamp ou +days).
    Body JSON: { "license_key": "...", "days": 30 }
    """
    data = request.get_json(force=True, silent=False)
    license_key = str(data.get("license_key", "")).strip()
    days        = data.get("days", None)

    if not license_key:
        return jsonify({"error": "missing_license_key"}), 400
    if days is None:
        return jsonify({"error": "missing_days"}), 400

    now = int(time.time())
    expires_at = now + int(days) * 86400
    set_license_expiry(license_key, expires_at)
    return jsonify({"status": "OK", "expires_at": expires_at}), 200

# ---------------------- Ancienne vérif POST JSON (non utilisée par EA actuel) ----------------------

@app.route("/api/verify", methods=["POST"])
def api_verify():
    """
    Vérification de licence appelée par l'EA MT5 (ancienne méthode POST JSON).
    Body JSON attendu:
    {
      "license_key": "...",
      "account": "62094495",
      "symbol": "US30"
    }
    """
    try:
        data = request.get_json(force=False, silent=True)

        if not isinstance(data, dict):
            raw = (request.data or b"").decode("utf-8", "ignore")
            raw = raw.strip("\x00")
            log.info("api_verify: RAW body = %r", raw)
            try:
                data = json.loads(raw)
            except Exception as e:
                log.error("api_verify: JSON parse error: %s", e)
                return jsonify({"status": "DENIED", "reason": "bad_json"}), 200

    except Exception as e:
        log.error("api_verify: get_json failed: %s", e)
        return jsonify({"status": "DENIED", "reason": "bad_json"}), 200

    license_key = str(data.get("license_key", "")).strip()
    account     = str(data.get("account", "")).strip()
    symbol      = str(data.get("symbol", "")).strip()

    if not license_key:
        return jsonify({"status": "DENIED", "reason": "missing_license_key"}), 200

    lic = find_license(license_key)
    if not lic:
        return jsonify({"status": "DENIED", "reason": "unknown_license"}), 200

    if lic["status"] != "active":
        return jsonify({"status": "DENIED", "reason": "inactive"}), 200

    now = int(time.time())
    exp = lic.get("expires_at")
    if exp is not None and int(exp) > 0 and now > int(exp):
        set_license_status(license_key, "expired")
        return jsonify({"status": "DENIED", "reason": "expired"}), 200

    lic = bind_account_if_needed(license_key, account)
    if lic and lic.get("mt5_account") and lic["mt5_account"] != account:
        return jsonify({"status": "DENIED", "reason": "wrong_account"}), 200

    return jsonify({"status": "OK"}), 200

# ---------------------- GET pour l'EA actuel ----------------------

@app.route("/api/check_license", methods=["GET"])
def api_check_license():
    """
    Vérification simple GET utilisée par le nouvel EA.

    GET /api/check_license?license_key=XXXX
    """
    license_key = str(request.args.get("license_key", "")).strip()

    if not license_key:
        return jsonify({
            "ok": False,
            "valid": False,
            "reason": "missing_license_key",
            "email": None,
            "expires_at": None,
            "expires_at_iso": None,
        }), 400

    lic = find_license(license_key)
    now = int(time.time())

    if not lic:
        return jsonify({
            "ok": True,
            "valid": False,
            "reason": "not_found",
            "email": None,
            "expires_at": None,
            "expires_at_iso": None,
        }), 200

    email      = lic.get("email")
    status     = lic.get("status", "")
    expires_at = lic.get("expires_at") or 0
    expires_at = int(expires_at)

    expires_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(expires_at)) if expires_at > 0 else None

    if status != "active":
        return jsonify({
            "ok": True,
            "valid": False,
            "reason": "inactive",
            "email": email,
            "expires_at": expires_at if expires_at > 0 else None,
            "expires_at_iso": expires_iso,
        }), 200

    if expires_at > 0 and now > expires_at:
        set_license_status(license_key, "expired")
        return jsonify({
            "ok": True,
            "valid": False,
            "reason": "expired",
            "email": email,
            "expires_at": expires_at,
            "expires_at_iso": expires_iso,
        }), 200

    return jsonify({
        "ok": True,
        "valid": True,
        "reason": "ok",
        "email": email,
        "expires_at": expires_at if expires_at > 0 else None,
        "expires_at_iso": expires_iso,
    }), 200

# ---------------------- Stripe Checkout (création paiement) ----------------------

@app.route("/api/create_checkout", methods=["POST"])
def api_create_checkout():
    """
    Crée une session Stripe Checkout pour un abonnement ProfitPro.

    POST /api/create_checkout
    Body JSON: { "email": "client@profitpro.io" }

    Réponse:
      { "ok": true, "checkout_url": "https://checkout.stripe.com/..." }
    """
    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"ok": False, "error": "bad_json"}), 400

    email = str(data.get("email", "")).strip()

    if not email:
        return jsonify({"ok": False, "error": "missing_email"}), 400

    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        return jsonify({"ok": False, "error": "stripe_not_configured"}), 500

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer_email=email,
            line_items=[{
                "price": STRIPE_PRICE_ID,
                "quantity": 1,
            }],
            success_url=STRIPE_SUCCESS_URL + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=STRIPE_CANCEL_URL,
        )
    except Exception as e:
        log.exception("Erreur Stripe lors de la création de la session Checkout")
        return jsonify({"ok": False, "error": "stripe_error", "detail": str(e)}), 500

    return jsonify({"ok": True, "checkout_url": session.url}), 200

# ---------------------- Webhook Stripe ----------------------

@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    """
    Webhook Stripe.
    Utilisé pour créer une licence quand un paiement est confirmé.
    """
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature", "")

    if not STRIPE_WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "missing_webhook_secret"}), 500

    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            STRIPE_WEBHOOK_SECRET,
        )
    except stripe.error.SignatureVerificationError:
        log.warning("Webhook Stripe: signature invalide")
        return jsonify({"ok": False, "error": "bad_signature"}), 400
    except Exception as e:
        log.exception("Erreur parsing webhook Stripe")
        return jsonify({"ok": False, "error": "webhook_error", "detail": str(e)}), 400

    event_type  = event.get("type")
    data_object = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        email = (
            data_object.get("customer_details", {}).get("email")
            or data_object.get("customer_email")
        )
        if email:
            try:
                key = create_license(email=email, days_valid=30)
                log.info("Licence créée via Stripe pour %s -> %s", email, key)
            except Exception:
                log.exception("Erreur création licence depuis webhook")

    return jsonify({"ok": True}), 200

# ------------------------- ping + Main --------------------------

@app.route("/api/ping", methods=["GET"])
def api_ping():
    """Test simple pour vérifier que le serveur répond."""
    return jsonify({"ok": True, "service": "license_server"}), 200


if __name__ == "__main__":
    init_db()
    log.info("License server démarré sur http://127.0.0.1:7000")
    app.run(host="0.0.0.0", port=7000, debug=False)