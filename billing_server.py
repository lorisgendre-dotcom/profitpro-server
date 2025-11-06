#!/usr/bin/env python3
# billing_server.py — passerelle Stripe <-> licences ProfitPro
#
# Rôles :
# - /api/billing/create_checkout : crée une session Checkout d’abonnement
# - /api/stripe/webhook : réagit aux événements Stripe pour activer / prolonger
#                         ou désactiver les licences
#
# IMPORTANT :
# - Ce fichier NE remplace PAS license_server.py
# - Il UTILISE le même fichier licenses.db

import time
import sqlite3
import logging

from flask import Flask, request, jsonify
import stripe

# -------------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------------

DB_PATH = "licenses.db"

# Clé secrète Stripe (test)  ← DOIT être ta clé sk_test_...
STRIPE_SECRET_KEY = "sk_test_51SQ3CtGnb6xR5GqT3DnmoPwmK3y21Hx6U1oB8JUJEUmSNH4wJ7WqQ18GTg1v1Rjr5Hi0sqkQPaVccsLgML5f3lWl00RrUFhsNw"

# ID de prix d’abonnement mensuel (19,90 € en test)
STRIPE_PRICE_ID = "price_1SQ4ewGnb6xR5GqT5VRn51Ir"

# Clé de signature du webhook (à récupérer dans le dashboard Stripe)
STRIPE_WEBHOOK_SECRET = "whsec_5e542d2f07e5fc70c02dfc3c2437f7cd1ad7b0b73249ebee7fe103e8bc05b370"

# URL de retour après paiement (à remplacer par ton futur site)
CHECKOUT_SUCCESS_URL = "https://example.com/success?session_id={CHECKOUT_SESSION_ID}"
CHECKOUT_CANCEL_URL  = "https://example.com/cancel"

stripe.api_key = STRIPE_SECRET_KEY

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("billing_server")

app = Flask(__name__)

# -------------------------------------------------------------------
# FONCTIONS LICENCE (même DB que license_server.py)
# -------------------------------------------------------------------

def db_connect():
    return sqlite3.connect(DB_PATH)


def find_active_license_by_email(email: str):
    conn = db_connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM licenses WHERE email = ? AND status = 'active'",
        (email,)
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def deactivate_licenses_by_email(email: str):
    """Passe toutes les licences actives de cet email en 'inactive'."""
    now = int(time.time())
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE licenses SET status = 'inactive', updated_at = ? WHERE email = ? AND status = 'active'",
        (now, email),
    )
    affected = cur.rowcount
    conn.commit()
    conn.close()
    log.info("Licences désactivées pour %s -> %s lignes", email, affected)


def create_or_extend_license_for_email(email: str, days_valid: int = 30) -> str:
    """
    - Si le client n’a PAS encore de licence active -> on en crée une (N jours)
    - S’il a déjà une licence active -> on pousse la date d’expiration de +N jours
    Retourne la license_key.
    """
    from license_server import create_license, set_license_expiry  # réutilise tes fonctions

    now = int(time.time())
    lic = find_active_license_by_email(email)
    if not lic:
        key = create_license(email=email, days_valid=days_valid)
        log.info("Licence créée pour %s via Stripe -> %s", email, key)
        return key

    old_key = lic["license_key"]
    old_exp = lic.get("expires_at") or now
    new_exp = max(old_exp, now) + days_valid * 86400
    set_license_expiry(old_key, new_exp)
    log.info("Licence étendue pour %s -> %s (expire le %s)", email, old_key, time.ctime(new_exp))
    return old_key

# -------------------------------------------------------------------
# ENDPOINTS BILLING
# -------------------------------------------------------------------

@app.route("/api/billing/health", methods=["GET"])
def billing_health():
    return jsonify({"status": "OK"}), 200


@app.route("/api/billing/create_checkout", methods=["POST"])
def create_checkout():
    """
    Crée une session Stripe Checkout pour un abonnement ProfitPro US30.
    Body JSON attendu : { "email": "client@exemple.com", "days": 30 }

    Retourne : { "checkout_url": "https://checkout.stripe.com/..." ,
                 "license_key": "...",
                 "price_id": "..." }
    """
    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"error": "bad_json"}), 400

    email = str(data.get("email", "")).strip()
    days  = int(data.get("days", 30))

    if not email:
        return jsonify({"error": "missing_email"}), 400

    # Crée / étend une licence côté DB immédiatement
    license_key = create_or_extend_license_for_email(email, days_valid=days)

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            customer_email=email,
            line_items=[{
                "price": STRIPE_PRICE_ID,
                "quantity": 1,
            }],
            success_url=CHECKOUT_SUCCESS_URL,
            cancel_url=CHECKOUT_CANCEL_URL,
        )
    except Exception as e:
        log.error("Erreur Stripe Checkout : %s", e)
        return jsonify({"error": "stripe_error", "detail": str(e)}), 500

    log.info("Session Checkout créée pour %s -> %s", email, session.id)
    return jsonify({
        "checkout_url": session.url,
        "license_key":  license_key,
        "price_id":     STRIPE_PRICE_ID
    }), 200

# -------------------------------------------------------------------
# WEBHOOK STRIPE
# -------------------------------------------------------------------

@app.route("/api/stripe/webhook", methods=["POST"])
def stripe_webhook():
    """
    Réception des événements Stripe.
    Utilise STRIPE_WEBHOOK_SECRET pour vérifier la signature.
    Événements gérés :
      - checkout.session.completed      -> crée / étend licence
      - invoice.payment_succeeded       -> étend licence
      - invoice.payment_failed          -> désactive licence
      - customer.subscription.deleted   -> désactive licence
    """
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature", "")

    if not STRIPE_WEBHOOK_SECRET or STRIPE_WEBHOOK_SECRET.startswith("whsec_A_REMPLACER"):
        log.error("STRIPE_WEBHOOK_SECRET non configuré.")
        return jsonify({"error": "webhook_secret_not_configured"}), 500

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        log.error("Webhook Stripe signature invalide / parsing error : %s", e)
        return jsonify({"error": "invalid_signature"}), 400

    event_type = event.get("type", "")
    obj = event.get("data", {}).get("object", {})

    log.info("Webhook Stripe reçu : %s", event_type)

    # 1) Checkout complété
    if event_type == "checkout.session.completed":
        email = (
            (obj.get("customer_details") or {}).get("email")
            or obj.get("customer_email")
            or ""
        ).strip()
        if email:
            create_or_extend_license_for_email(email, days_valid=30)
            log.info("Licence créée/étendue (checkout.completed) pour %s", email)

    # 2) Facture payée (renouvellement d’abonnement)
    elif event_type == "invoice.payment_succeeded":
        email = str(obj.get("customer_email", "")).strip()
        if email:
            create_or_extend_license_for_email(email, days_valid=30)
            log.info("Licence étendue (invoice.payment_succeeded) pour %s", email)

    # 3) Paiement échoué -> on désactive
    elif event_type == "invoice.payment_failed":
        email = str(obj.get("customer_email", "")).strip()
        if email:
            deactivate_licenses_by_email(email)
            log.info("Licences désactivées (invoice.payment_failed) pour %s", email)

    # 4) Abonnement annulé -> on désactive
    elif event_type == "customer.subscription.deleted":
        customer_id = obj.get("customer")
        email = ""
        if customer_id:
            try:
                cust = stripe.Customer.retrieve(customer_id)
                email = str(cust.get("email", "")).strip()
            except Exception as e:
                log.error("Erreur récupération client Stripe %s : %s", customer_id, e)
        if email:
            deactivate_licenses_by_email(email)
            log.info("Licences désactivées (subscription.deleted) pour %s", email)

    return jsonify({"received": True}), 200

# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------

if __name__ == "__main__":
    log.info("Billing server démarré sur http://127.0.0.1:7100")
    app.run(host="0.0.0.0", port=7100, debug=False)