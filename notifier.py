import os
import urllib.request
import json
import logging
import time

LOG = logging.getLogger("pokemon-restock")

def send_discord_alert(webhook_url, message, embed_data=None, bot_name=None, bot_avatar=None, retries=3, delay=2):
    """Envoie une alerte propre sur un webhook Discord avec personnalisation du bot et gestion des retries."""
    if not webhook_url:
        return False
    
    payload = {"content": message}
    
    # Force le nom du bot spécifique au salon si fourni
    if bot_name:
        payload["username"] = bot_name
    # Force l'avatar/icône du bot spécifique au salon si fourni
    if bot_avatar:
        payload["avatar_url"] = bot_avatar
        
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
    
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status in (200, 204):
                    return True
        except urllib.error.HTTPError as e:
            if e.code in (502, 503, 504) and attempt < retries - 1:
                time.sleep(delay)
                continue
            LOG.error("Erreur HTTP %s lors de l'envoi de l'alerte Discord : %s", e.code, e)
            break
        except Exception as e:
            LOG.error("Erreur lors de l'envoi de l'alerte Discord : %s", e)
            break
            
    return False

def notifier_dashboard_flask():
    """Prévient l'application Flask locale qu'un nouveau stock est dispo."""
    try:
        url = "http://127.0.0.1:5000/refresh-stock"
        req = urllib.request.Request(url, method="POST")
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass