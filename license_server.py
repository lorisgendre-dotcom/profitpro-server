#!/usr/bin/env python3
# license_server.py — gestion des licences ProfitPro (avec expiration)

import os
import sqlite3
import secrets
import time
import logging
import json

from flask import Flask, request, jsonify, send_from_directory
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

# ---------------------- Landing page (root) ----------------------

@app.route("/")
def landing_page():
    """Sert le fichier us30.html à la racine du site."""
    return send_from_directory(app.root_path, "us30.html")

# ---------------------- Petite page de paiement + page succès ----------------------

@app.route("/pay", methods=["GET"])
def pay_page():
    """
    Page de paiement stylée, même thème que la landing.
    Elle appelle /api/create_checkout puis redirige vers Stripe Checkout.
    """
    return """
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Abonnement PROFITPRO US30 – Paiement</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    * { box-sizing:border-box; margin:0; padding:0; }
    body {
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 0% 0%, rgba(37,99,235,0.30), transparent 55%),
        radial-gradient(circle at 100% 0%, rgba(37,99,235,0.20), transparent 55%),
        #020617;
      min-height: 100vh;
      display:flex;
      align-items:center;
      justify-content:center;
      color:#e5e7eb;
    }
    .wrap {
      width:100%;
      max-width:420px;
      padding:24px 16px;
    }
    .card {
      background:
        radial-gradient(circle at 0% 0%, rgba(37,99,235,0.35), transparent 60%),
        radial-gradient(circle at 120% 0%, rgba(56,189,248,0.26), transparent 60%),
        #020617;
      border-radius:24px;
      border:1px solid rgba(148,163,184,0.35);
      padding:26px 22px 24px;
      box-shadow:0 22px 70px rgba(15,23,42,0.95);
    }
    .logo {
      display:flex;
      justify-content:center;
      margin-bottom:16px;
    }
    .logo img {
      height:48px;
      width:auto;
      border-radius:14px;
      box-shadow:0 0 25px rgba(37,99,235,0.9);
    }
    h1 {
      font-size:20px;
      text-align:center;
      margin-bottom:6px;
    }
    .price {
      font-size:13px;
      text-align:center;
      color:#9ca3af;
      margin-bottom:18px;
    }
    .field-label {
      font-size:13px;
      color:#9ca3af;
      margin-bottom:6px;
    }
    .input {
      width:100%;
      padding:10px 12px;
      border-radius:999px;
      border:1px solid rgba(148,163,184,0.45);
      background:rgba(15,23,42,0.95);
      color:#e5e7eb;
      font-size:14px;
      outline:none;
    }
    .input:focus {
      border-color:#3b82f6;
      box-shadow:0 0 0 1px rgba(59,130,246,0.6);
    }
    .btn {
      margin-top:14px;
      width:100%;
      padding:11px 18px;
      border-radius:999px;
      border:1px solid rgba(59,130,246,0.7);
      background:linear-gradient(135deg,#2563eb,#4f46e5);
      color:#f9fafb;
      font-size:14px;
      font-weight:600;
      cursor:pointer;
      box-shadow:0 18px 55px rgba(37,99,235,0.75);
      transition:transform 0.08s ease, box-shadow 0.08s ease;
    }
    .btn:hover {
      transform:translateY(-1px);
      box-shadow:0 22px 70px rgba(37,99,235,0.9);
    }
    .msg {
      margin-top:10px;
      font-size:12px;
      color:#f97373;
      min-height:16px;
    }
    .note {
      margin-top:12px;
      font-size:11px;
      color:#9ca3af;
      text-align:center;
    }
    .back {
      margin-top:14px;
      text-align:center;
      font-size:12px;
    }
    .back a { color:#93c5fd; text-decoration:none; }
    .back a:hover { text-decoration:underline; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="logo">
        <img src="/static/logo.jpeg" alt="PROFITPRO">
      </div>
      <h1>Abonnement PROFITPRO US30</h1>
      <p class="price">19,90 € / mois — environnement de test Stripe</p>

      <form id="pay-form">
        <div class="field">
          <div class="field-label">Ton email MT5</div>
          <input type="email" id="email" class="input" placeholder="email utilisé sur MT5" required>
        </div>
        <button type="submit" class="btn">Payer et s'abonner</button>
        <div id="msg" class="msg"></div>
      </form>

      <p class="note">
        Le paiement est géré par Stripe. Une licence sera créée automatiquement
        pour l'email saisi une fois le paiement validé.
      </p>
      <div class="back">
        <a href="/">← Revenir à la page d'accueil</a>
      </div>
    </div>
  </div>

  <script>
    const form = document.getElementById('pay-form');
    const msg  = document.getElementById('msg');

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      msg.textContent = "";
      const email = document.getElementById('email').value.trim();
      if (!email) {
        msg.textContent = "Merci de saisir un email.";
        return;
      }

      try {
        const resp = await fetch('/api/create_checkout', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ email: email })
        });

        const data = await resp.json();

        if (!data.ok) {
          msg.textContent = "Erreur: " + (data.error || "inconnue");
          console.error("Erreur /api/create_checkout:", data);
          return;
        }

        window.location.href = data.checkout_url;
      } catch (err) {
        console.error(err);
        msg.textContent = "Erreur réseau, réessaie plus tard.";
      }
    });
  </script>
</body>
</html>
"""

@app.route("/success", methods=["GET"])
def success_page():
    """
    Page affichée après un paiement validé Stripe.
    Stripe rajoute ?session_id=cs_test_xxx dans l'URL.
    """
    session_id = request.args.get("session_id", "")
    return """
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Paiement confirmé – PROFITPRO US30</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    * { box-sizing:border-box; margin:0; padding:0; }
    body {
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 0% 0%, rgba(37,99,235,0.30), transparent 55%),
        radial-gradient(circle at 100% 0%, rgba(37,99,235,0.20), transparent 55%),
        #020617;
      min-height: 100vh;
      display:flex;
      align-items:center;
      justify-content:center;
      color:#e5e7eb;
    }
    .wrap {
      width:100%;
      max-width:420px;
      padding:24px 16px;
    }
    .card {
      background:
        radial-gradient(circle at 0% 0%, rgba(37,99,235,0.35), transparent 60%),
        radial-gradient(circle at 120% 0%, rgba(56,189,248,0.26), transparent 60%),
        #020617;
      border-radius:24px;
      border:1px solid rgba(148,163,184,0.35);
      padding:26px 22px 24px;
      box-shadow:0 22px 70px rgba(15,23,42,0.95);
      text-align:center;
    }
    .logo {
      display:flex;
      justify-content:center;
      margin-bottom:14px;
    }
    .logo img {
      height:40px;
      width:auto;
      border-radius:12px;
      box-shadow:0 0 20px rgba(37,99,235,0.9);
    }
    h1 {
      font-size:20px;
      margin-bottom:6px;
    }
    .txt {
      font-size:13px;
      color:#9ca3af;
      margin-bottom:14px;
    }
    .btn {
      display:inline-block;
      margin-top:4px;
      padding:9px 18px;
      border-radius:999px;
      border:1px solid rgba(59,130,246,0.7);
      background:linear-gradient(135deg,#2563eb,#4f46e5);
      color:#f9fafb;
      font-size:13px;
      font-weight:600;
      cursor:pointer;
      box-shadow:0 14px 45px rgba(37,99,235,0.75);
      text-decoration:none;
    }
    .btn:hover {
      box-shadow:0 18px 60px rgba(37,99,235,0.9);
    }
    .session {
      margin-top:14px;
      font-size:11px;
      color:#6b7280;
      word-break:break-all;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="logo">
        <img src="/static/logo.jpeg" alt="PROFITPRO">
      </div>
      <h1>Paiement confirmé ✅</h1>
      <p class="txt">
        Merci, ton abonnement PROFITPRO US30 est bien enregistré (environnement de test Stripe).
        Une licence sera créée pour l'adresse email utilisée lors du paiement.
      </p>
      <a href="/" class="btn">Revenir à la page d'accueil</a>
      <p class="session">
        Session Stripe : """ + session_id + """
      </p>
    </div>
  </div>
</body>
</html>
"""

# ---------------------- Vérification licences ----------------------

@app.route("/api/admin/list_licenses", methods=["GET"])
def api_list_licenses():
    """Retourne toutes les licences enregistrées (usage interne uniquement)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM licenses ORDER BY created_at DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows), 200

# ------------------------- ping + Main --------------------------

@app.route("/api/ping", methods=["GET"])
def api_ping():
    """Test simple pour vérifier que le serveur répond."""
    return jsonify({"ok": True, "service": "license_server"}), 200


if __name__ == "__main__":
    init_db()
    log.info("License server démarré sur http://127.0.0.1:7000")
    app.run(host="0.0.0.0", port=7000, debug=False)