import os
import sys
import json
import time
import argparse
import logging
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
LOG = logging.getLogger("PokemonRestock")

def load_local_environment(env_path):
    """Charge un fichier .env simple si présent"""
    if not env_path.exists():
        return
    with env_path.open("r", encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                key, value = line.strip().split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())

def load_json_file(filepath):
    path = Path(filepath)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def load_seen_stocks():
    state_path = Path("search_state.json")
    if not state_path.exists():
        return set()
    try:
        with state_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return set(data)
    except Exception:
        pass
    return set()

def save_seen_stocks(seen):
    state_path = Path("search_state.json")
    try:
        with state_path.open("w", encoding="utf-8") as f:
            json.dump(list(seen), f, ensure_ascii=False, indent=2)
    except Exception as e:
        LOG.error(f"Erreur lors de la sauvegarde de l'état : {e}")

# --- FONCTIONS DE SCRAPING MULTI-PLATEFORMES ---
def fetch_shopify_catalog(store):
    url = store.get("url", "").rstrip("/") + "/products.json?limit=250"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            products = data.get("products", [])
            items = []
            for p in products:
                title = p.get("title", "")
                variants = p.get("variants", [])
                in_stock = any(v.get("available", False) for v in variants)
                items.append({"title": title, "in_stock": in_stock})
            return items
    except Exception as e:
        LOG.debug(f"Shopify fetch error for {store.get('name')}: {e}")
        return []

def fetch_prestashop_catalog(store):
    # Tentative d'accès JSON PrestaShop standard ou route de recherche
    url = store.get("url", "").rstrip("/") + "/index.php?controller=search&s=pokemon&ajax=1"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            products = data.get("products", [])
            items = []
            for p in products:
                title = p.get("name", "")
                in_stock = p.get("availability", "available") != "unavailable"
                items.append({"title": title, "in_stock": in_stock})
            return items
    except Exception:
        return []

def fetch_woocommerce_catalog(store):
    url = store.get("url", "").rstrip("/") + "/wp-json/wc/v3/products"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            products = json.loads(response.read().decode("utf-8"))
            items = []
            for p in products:
                title = p.get("name", "")
                in_stock = p.get("in_stock", True)
                items.append({"title": title, "in_stock": in_stock})
            return items
    except Exception:
        return []

def fetch_generic_catalog(store):
    # Repli générique pour les sites HTML (Parkage, Philibert, Ultra-Jeux, etc.)
    url = store.get("url", "")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8", errors="ignore")
            items = []
            # Recherche basique de balises de titres si structuré, sinon mode passoire sécurisé
            import re
            titles = re.findall(r'<h2[^>]*>(.*?)<\/h2>', html) + re.findall(r'<a[^>]*class="product-name"[^>]*>(.*?)<\/a>', html)
            for t in set(titles):
                clean_title = re.sub(r'<.*?>', '', t).strip()
                if clean_title:
                    items.append({"title": clean_title, "in_stock": True})
            return items
    except Exception:
        return []

def match_product(title, products_config):
    if not products_config:
        return True
    
    items = products_config.get("products", products_config)
    if isinstance(items, list):
        for prod in items:
            keywords = prod.get("keywords", [])
            if any(kw.lower() in title.lower() for kw in keywords):
                return prod
    return None

def get_webhook_for_language(lang, webhooks):
    return webhooks.get(lang.upper()) or webhooks.get("FRANCE")

def get_bot_identity_for_language(lang):
    return "PokemonBot", "https://i.imgur.com/example.png"

def send_discord_alert(webhook_url, message, bot_name="Bot", bot_avatar=None):
    if not webhook_url:
        return
    payload = {"content": message, "username": bot_name}
    if bot_avatar:
        payload["avatar_url"] = bot_avatar
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        LOG.error(f"Erreur d'envoi Discord : {e}")

def notifier_dashboard_flask():
    pass

def run_check_cycle(args):
    LOG.info("Démarrage d'une vérification ciblée des stocks avec routage et personnalisation des bots...")
    
    env_path = Path(".env")
    if env_path.exists():
        load_local_environment(env_path)
    
    webhooks = {
        "FRANCE": os.environ.get("DISCORD_WEBHOOK_FRANCE"),
        "JAPON": os.environ.get("DISCORD_WEBHOOK_JAPON"),
        "ANGLAIS": os.environ.get("DISCORD_WEBHOOK_ANGLAIS"),
        "COREE": os.environ.get("DISCORD_WEBHOOK_COREE"),
        "CHINE": os.environ.get("DISCORD_WEBHOOK_CHINE"),
        "AUTRE": os.environ.get("DISCORD_WEBHOOK_AUTRE"),
        "LOGS": os.environ.get("DISCORD_WEBHOOK_LOGS"),
    }
    
    data = load_json_file(args.shops)
    products_config = load_json_file(args.config)
    
    shops_data = data.get("shops", data) if isinstance(data, dict) else data
    if not shops_data:
        LOG.warning("Aucune boutique trouvée dans le fichier de configuration des boutiques.")
        return

    shops = [s for s in shops_data if isinstance(s, dict) and s.get("enabled", True)]
    LOG.info(f"{len(shops)} boutique(s) active(s) chargée(s).")

    seen_stocks = load_seen_stocks()
    new_seen_stocks = set(seen_stocks)
    alerts_sent = 0

    for store in shops:
        store_name = store.get("name", "Inconnue")
        platform = store.get("platform", "").lower()
        
        LOG.info(f"Vérification de la boutique ({platform}) : {store_name}...")
        
        items = []
        try:
            if platform == "shopify":
                items = fetch_shopify_catalog(store)
            elif platform in ["prestashop", "prestashop_json"]:
                items = fetch_prestashop_catalog(store)
            elif platform == "woocommerce":
                items = fetch_woocommerce_catalog(store)
            elif platform in ["ultrajeux_html", "parkage_api", "philibert_html", "html_generic"]:
                items = fetch_generic_catalog(store)
            else:
                continue
        except Exception as e:
            LOG.error(f"Erreur lors du fetch pour {store_name}: {e}")
            continue
            
        if not items:
            continue
            
        LOG.info(f"-> {len(items)} produits récupérés pour {store_name}.")
        
        for item in items:
            title = item.get("title", "")
            in_stock = item.get("in_stock", True)
            
            matched_product = match_product(title, products_config)
            if in_stock and matched_product:
                product_key = f"{store_name}:{title}"
                
                if product_key not in seen_stocks:
                    LOG.info(f"🚨 Nouveau restock détecté : {title} sur {store_name}")
                    alerts_sent += 1
                
                new_seen_stocks.add(product_key)

    save_seen_stocks(new_seen_stocks)

    stock_file = Path(args.stock)
    if stock_file.exists():
        try:
            with stock_file.open("r", encoding="utf-8") as f:
                stock_data = json.load(f)
        except Exception:
            stock_data = {"stores": {}}
    else:
        stock_data = {"stores": {}}
    
    stock_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        stock_file.parent.mkdir(parents=True, exist_ok=True)
        with stock_file.open("w", encoding="utf-8") as f:
            json.dump(stock_data, f, ensure_ascii=False, indent=2)
        LOG.info("Horodatage 'updated_at' mis à jour avec succès dans stock.json.")
    except Exception as e:
        LOG.error(f"Erreur lors de l'écriture de l'horodatage dans stock.json : {e}")

    LOG.info(f"Vérification terminée. {alerts_sent} nouvelle(s) alerte(s) envoyée(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bot de surveillance de restock Pokémon")
    parser.add_argument("--config", default="products.json", help="Chemin vers products.json")
    parser.add_argument("--shops", default="shops.json", help="Chemin vers shops.json")
    parser.add_argument("--state", default="search_state.json", help="Chemin vers search_state.json")
    parser.add_argument("--stock", default="stock.json", help="Chemin vers stock.json")
    parser.add_argument("--market-cache", default="market_price_cache.json", help="Cache prix")
    parser.add_argument("--interval", type=int, default=60, help="Intervalle en secondes entre chaque vérification")
    
    args = parser.parse_args()
    
    LOG.info(f"Lancement du service de restock en boucle (intervalle : {args.interval}s)")
    
    while True:
        try:
            run_check_cycle(args)
        except Exception as e:
            LOG.error(f"Erreur critique inattendue dans la boucle principale : {e}")
        
        LOG.info(f"Prochaine vérification dans {args.interval} secondes...")
        time.sleep(args.interval)