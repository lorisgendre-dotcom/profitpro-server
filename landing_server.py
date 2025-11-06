#!/usr/bin/env python3
# landing_server.py — page vitrine + capture email + redirection vers Stripe Checkout

import os
from flask import Flask, render_template_string, request, redirect, jsonify
import requests

app = Flask(__name__)

# URL de ton endpoint de facturation Stripe (billing_server)
BILLING_API_URL = "http://127.0.0.1:7100/api/billing/create_checkout"

# ------------------- PAGE HTML -------------------
HTML_PAGE = """
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ProfitPro - Robot US30 Automatisé</title>
  <style>
    body { font-family: Arial, sans-serif; background:#0b0b0b; color:white; text-align:center; margin:0; padding:0; }
    h1 { font-size:2em; margin-top:50px; }
    p { font-size:1.1em; color:#ccc; }
    form { margin-top:40px; }
    input[type=email] {
      padding:12px; border:none; width:300px; font-size:1em; border-radius:5px;
    }
    button {
      padding:12px 25px; background:#0af; border:none; color:white;
      font-size:1em; border-radius:5px; cursor:pointer;
    }
    button:hover { background:#09c; }
    .footer { margin-top:80px; font-size:0.9em; color:#555; }
  </style>
</head>
<body>
  <h1>🚀 ProfitPro — Le robot US30 qui trade pour vous</h1>
  <p>Connectez-le à votre compte MetaTrader 5 et laissez-le exécuter les trades automatiquement.<br>
  Abonnement mensuel : <b>19,90€</b> — Résiliable à tout moment.</p>

  <form method="POST" action="/subscribe">
    <input type="email" name="email" placeholder="Votre adresse e-mail" required>
    <button type="submit">Démarrer maintenant</button>
  </form>

  <div class="footer">ProfitPro © 2025 — Trading automatisé</div>
</body>
</html>
"""

# ------------------- ROUTES -------------------

@app.route("/", methods=["GET"])
def home():
    return render_template_string(HTML_PAGE)

@app.route("/subscribe", methods=["POST"])
def subscribe():
    email = request.form.get("email", "").strip()
    if not email:
        return "Email manquant", 400

    # Appel API vers billing_server
    try:
        resp = requests.post(
            BILLING_API_URL,
            json={"email": email, "days": 30},
            timeout=10
        )
        data = resp.json()
        if "checkout_url" in data:
            return redirect(data["checkout_url"])
        else:
            return jsonify(data), 500
    except Exception as e:
        return f"Erreur serveur : {e}", 500

# ------------------- MAIN -------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7200, debug=False)