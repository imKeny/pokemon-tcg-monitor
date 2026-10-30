import os
import urllib.request
import json
import logging

LOG = logging.getLogger("pokemon-restock")

def send_discord_alert(webhook_url, message, embed_data=None):
    """Envoie une alerte propre sur un webhook Discord."""
    if not webhook_url:
        return False
    
    payload = {"content": message}
    if embed_data:
        payload["embeds"] = [embed_data]

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status in (200, 204)
    except Exception as e:
        LOG.error("Erreur lors de l'envoi de l'alerte Discord : %s", e)
        return False

def notifier_dashboard_flask():
    """Prévient l'application Flask locale qu'un nouveau stock est dispo."""
    try:
        url = "http://127.0.0.1:5000/refresh-stock"
        req = urllib.request.Request(url, method="POST")
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        # Si le dashboard ne tourne pas en local, on ignore silencieusement
        pass