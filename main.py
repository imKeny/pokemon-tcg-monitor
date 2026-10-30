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

def get_webhook_for_language(lang, webhooks):
    """Détermine le bon webhook en fonction de la langue du produit."""
    lang = (lang or "").strip().upper()
    if lang in ["FR", "FRANCAIS", "FRENCH"]:
        return webhooks.get("FRANCE")
    elif lang in ["JP", "JAPON", "JAPANESE"]:
        return webhooks.get("JAPON")
    elif lang in ["EN", "ANGLAIS", "ENGLISH", "US", "UK"]:
        return webhooks.get("ANGLAIS")
    elif lang in ["KR", "COREE", "KOREAN"]:
        return webhooks.get("COREE")
    elif lang in ["CN", "CHINE", "CHINESE"]:
        return webhooks.get("CHINE")
    else:
        return webhooks.get("FRANCE") or webhooks.get("AUTRE")

def get_bot_identity_for_language(lang):
    """Associe chaque langue/région au nom du bot et à son icône dédiée."""
    lang = (lang or "").strip().upper()
    if lang in ["FR", "FRANCAIS", "FRENCH"]:
        return "Bot France", "https://i.imgur.com/example_fr.png"
    elif lang in ["JP", "JAPON", "JAPANESE"]:
        return "Bot Japon", "https://i.imgur.com/example_jp.png"
    elif lang in ["EN", "ANGLAIS", "ENGLISH", "US", "UK"]:
        return "Bot Anglais", "https://i.imgur.com/example_en.png"
    elif lang in ["KR", "COREE", "KOREAN"]:
        return "Bot Corée", "https://i.imgur.com/example_kr.png"
    elif lang in ["CN", "CHINE", "CHINESE"]:
        return "Bot Chine", "https://i.imgur.com/example_cn.png"
    else:
        return "Bot Autre", "https://i.imgur.com/example_autre.png"

def main():
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
    
    data = load_json_file("shops.json")
    products_config = load_json_file("products.json")
    
    shops_data = data.get("shops", data) if isinstance(data, dict) else data
    if not shops_data:
        LOG.warning("Aucune boutique trouvée dans shops.json.")
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
            if webhooks["LOGS"]:
                send_discord_alert(
                    webhooks["LOGS"], 
                    f"⚠️ **{store_name}** : Erreur technique pendant la lecture du catalogue\n`{e}`",
                    bot_name="Logs Bot",
                    bot_avatar="https://i.imgur.com/example_porygon.png"
                )
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
                    
                    product_lang = "FR"
                    if isinstance(matched_product, dict):
                        product_lang = matched_product.get("lang", "FR")
                    
                    target_webhook = get_webhook_for_language(product_lang, webhooks)
                    bot_name, bot_avatar = get_bot_identity_for_language(product_lang)
                    
                    if target_webhook:
                        send_discord_alert(
                            target_webhook, 
                            f"🚨 **Restock !**\nProduit : {title}\nBoutique : {store_name}",
                            bot_name=bot_name,
                            bot_avatar=bot_avatar
                        )
                    
                    notifier_dashboard_flask()
                    alerts_sent += 1
                
                new_seen_stocks.add(product_key)

    save_seen_stocks(new_seen_stocks)
    LOG.info(f"Vérification terminée. {alerts_sent} nouvelle(s) alerte(s) envoyée(s).")

if __name__ == "__main__":
    main()