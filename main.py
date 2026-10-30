import os
import json
import logging
from pathlib import Path
from config import load_local_environment
from scraper import fetch_shopify_catalog
from prestashop_scraper import fetch_prestashop_catalog
from woocommerce_scraper import fetch_woocommerce_catalog
from html_scraper import fetch_generic_catalog
from matcher import match_product
from state_manager import load_seen_stocks, save_seen_stocks
from notifier import send_discord_alert, notifier_dashboard_flask

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("pokemon-restock")

def load_json_file(filename):
    path = Path(filename)
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            LOG.error(f"Erreur lecture {filename}: {e}")
    return {}

def main():
    LOG.info("Démarrage d'une vérification ciblée des stocks...")
    
    env_path = Path(".env")
    if env_path.exists():
        load_local_environment(env_path)
    
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    data = load_json_file("shops.json")
    products_config = load_json_file("products.json")
    
    shops_data = data.get("shops", data) if isinstance(data, dict) else data
    if not shops_data:
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
        if platform == "shopify":
            items = fetch_shopify_catalog(store)
        elif platform in ["prestashop", "prestashop_json"]:
            items = fetch_prestashop_catalog(store)
        elif platform == "woocommerce":
            items = fetch_woocommerce_catalog(store)
        elif platform in ["ultrajeux_html", "parkage_api", "philibert_html"]:
            items = fetch_generic_catalog(store)
        else:
            continue
            
        if not items:
            continue
            
        LOG.info(f"-> {len(items)} produits récupérés pour {store_name}.")
        
        for item in items:
            title = item.get("title", "")
            in_stock = item.get("in_stock", True)
            
            if in_stock and match_product(title, products_config):
                product_key = f"{store_name}:{title}"
                
                if product_key not in seen_stocks:
                    LOG.info(f"🚨 Nouveau restock détecté : {title} sur {store_name}")
                    if webhook_url:
                        send_discord_alert(webhook_url, f"🚨 **Restock !**\nProduit : {title}\nBoutique : {store_name}")
                    notifier_dashboard_flask()
                    alerts_sent += 1
                
                new_seen_stocks.add(product_key)

    save_seen_stocks(new_seen_stocks)
    LOG.info(f"Vérification terminée. {alerts_sent} nouvelle(s) alerte(s) envoyée(s).")

if __name__ == "__main__":
    main()