import os
import sys
import json
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
LOG = logging.getLogger("PokemonRestock")

# --- PLACE TES FONCTIONS EXISTANTES ICI ---
# (load_local_environment, load_json_file, load_seen_stocks, save_seen_stocks,
#  fetch_shopify_catalog, fetch_prestashop_catalog, fetch_woocommerce_catalog,
#  fetch_generic_catalog, match_product, get_webhook_for_language, 
#  get_bot_identity_for_language, send_discord_alert, notifier_dashboard_flask, etc.)

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
    
    # Utilisation des chemins passés en argument par Docker
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

    # Mise à jour de l'horodatage du dashboard dans stock.json
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
        
        LOG.info(prochain := f"Prochaine vérification dans {args.interval} secondes...")
        time.sleep(args.interval)