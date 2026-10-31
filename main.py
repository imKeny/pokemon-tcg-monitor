import os
import sys
import json
import time
import argparse
import logging
import re
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
LOG = logging.getLogger("PokemonRestock")

def load_local_environment(env_path):
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

def fetch_shopify_catalog(store):
    base_url = store.get("url", "").rstrip("/")
    url = base_url + "/products.json?limit=250"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            products = data.get("products", [])
            items = []
            for p in products:
                title = p.get("title", "")
                handle = p.get("handle", "")
                product_url = f"{base_url}/products/{handle}" if handle else base_url
                variants = p.get("variants", [])
                in_stock = any(v.get("available", False) for v in variants)
                
                price = "N/C"
                inventory = None
                for v in variants:
                    if v.get("price"):
                        price = f"{v.get('price')} €"
                    if v.get("inventory_quantity") is not None and v.get("inventory_quantity") > 0:
                        inventory = v.get("inventory_quantity")
                    break
                
                images = p.get("images", [])
                img_url = images[0].get("src", "") if images else ""
                items.append({
                    "title": title,
                    "in_stock": in_stock,
                    "price": price,
                    "stock": inventory,
                    "image": img_url,
                    "url": product_url
                })
            return items
    except Exception:
        return []

def fetch_woocommerce_catalog(store):
    base_url = store.get("url", "").rstrip("/")
    url = base_url + "/wp-json/wc/v3/products"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            products = json.loads(response.read().decode("utf-8"))
            items = []
            for p in products:
                title = p.get("name", "")
                in_stock = p.get("in_stock", True)
                price = f"{p.get('price')} €" if p.get('price') else "N/C"
                inventory = p.get("stock_quantity")
                images = p.get("images", [])
                img_url = images[0].get("src", "") if images else ""
                product_url = p.get("permalink", base_url)
                items.append({
                    "title": title,
                    "in_stock": in_stock,
                    "price": price,
                    "stock": inventory,
                    "image": img_url,
                    "url": product_url
                })
            return items
    except Exception:
        return []

def fetch_generic_catalog(store):
    base_url = store.get("url", "").rstrip("/")
    url = store.get("url", "")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8", errors="ignore")
            items = []
            product_blocks = re.findall(r'<div[^>]*class="[^"]*(?:product|item|card)[^"]*"[^>]*>(.*?)<\/div>', html, re.DOTALL | re.IGNORECASE)
            for block in product_blocks:
                title_match = re.search(r'<h[234][^>]*>(.*?)<\/h[234]>|<a[^>]*class="[^"]*name[^"]*"[^>]*>(.*?)<\/a>', block, re.IGNORECASE)
                if title_match:
                    raw_title = title_match.group(1) or title_match.group(2)
                    clean_title = re.sub(r'<.*?>', '', raw_title).strip()
                    if clean_title:
                        link_match = re.search(r'<a[^>]+href=["\']([^"\']+)["\']', block, re.IGNORECASE)
                        product_url = link_match.group(1) if link_match else base_url
                        if product_url and not product_url.startswith("http"):
                            parsed_url = urllib.parse.urlparse(url)
                            base_netloc = f"{parsed_url.scheme}://{parsed_url.netloc}"
                            product_url = base_netloc + product_url if product_url.startswith("/") else base_netloc + "/" + product_url

                        price_match = re.search(r'([\d\.,]+\s*€)', block)
                        price = price_match.group(1) if price_match else "N/C"

                        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', block, re.IGNORECASE)
                        img_url = img_match.group(1) if img_match else ""
                        if img_url and not img_url.startswith("http"):
                            parsed_url = urllib.parse.urlparse(url)
                            base_netloc = f"{parsed_url.scheme}://{parsed_url.netloc}"
                            img_url = base_netloc + img_url if img_url.startswith("/") else base_netloc + "/" + img_url

                        items.append({
                            "title": clean_title,
                            "in_stock": True,
                            "price": price,
                            "stock": None,
                            "image": img_url,
                            "url": product_url
                        })
            return items
    except Exception:
        return []

def fetch_store_data(store):
    platform = store.get("platform", "").lower()
    items = []
    try:
        if platform == "shopify":
            items = fetch_shopify_catalog(store)
        elif platform == "woocommerce":
            items = fetch_woocommerce_catalog(store)
        elif platform in ["philibert_html", "html_generic"]:
            items = fetch_generic_catalog(store)
    except Exception:
        pass
    return store, items

def match_product(title, products_config):
    title_lower = title.lower()

    if re.search(r'\d+/\d+', title):
        return None

    # 1. LISTE NOIRE ABSOLUE
    unwanted = [
        "valisette", "coffre aux trésors", "collector chest",
        "protection acrylique", "acrylique", "acrylic", "coussin", "pillow", "terrarium", "gemstone", 
        "carte pokémon", "carte pokemon", "carte unité", "carte holographique", "carte ultra rare", "carte rare", "carte commune", 
        "carte -", "carte à l'unité", "single", "psa", "bgs", "pca", "cgc", "gradée", "gradee", "certified",
        "figurine", "figure", "statue", "funism", "nendoroid", "pop!", "blind box", "toptoy",
        "sleeve", "protège", "protege", "deck protector", "tapis", "playmat", "classeur", "portfolio", "porte-clé",
        "yu gi oh", "yu-gi-oh", "one piece", "lorcana", "naruto", "saint seiya", 
        "panini", "stranger things", "disney", "pixar", "toy story", "super cg", 
        "dragon ball", "weiss schwarz", "riftbound", "league of legend", "roi lion", "adorable", "rise prestige"
    ]
    if any(u in title_lower for u in unwanted):
        return None

    if "carte" in title_lower and not any(sealed in title_lower for sealed in ["booster", "display", "coffret", "etb", "tripack", "duopack", "tin", "box", "boîte"]):
        return None

    if "pokemon" not in title_lower and "pokémon" not in title_lower:
        return None

    # 2. DÉTECTION STRICTE DE LA LANGUE (Japonais prioritaire absolu)
    detected_lang = "FRANCE"
    flag = "🇫🇷"
    
    jp_keywords = [
        " - jap", "-jap", "jap", "japonais", "japon", "🇯🇵", "(m1s)", "(m2)", "(m2a)", "(m4)", "(m5)", "(m6)", "(m6a)", 
        "stellar miracle", "mega brave", "japanese", "vmax climax", "s8b", "white flare", "black bolt", 
        "ruler of the black flame", "battle partners", "mega evolution pack", "mega symphonia", "sv11w", "sv11b", "sv9", "m6a"
    ]

    if any(term in title_lower for term in jp_keywords):
        detected_lang = "JAPON"
        flag = "🇯🇵"
    elif any(c in title for c in ["宝可梦", "盒", "包", "朱", "紫"]) or any(term in title_lower for term in ["[cn]", " cn ", "chine", "chinois", "🇨🇳", "cshc", "csfm", "chinese", "china"]):
        detected_lang = "CHINE"
        flag = "🇨🇳"
    elif any(term in title_lower for term in ["[en]", " en ", "anglais", "english", "🇬🇧", "booster bundle", "elite trainer box"]):
        detected_lang = "ANGLAIS"
        flag = "🇬🇧"
    elif any(term in title_lower for term in ["[kr]", " kr ", "coréen", "corée", "🇰🇷", "korean"]):
        detected_lang = "COREE"
        flag = "🇰🇷"

    return {"name": title, "language": detected_lang, "flag": flag}

def get_webhook_for_language(lang, webhooks):
    lang_upper = lang.upper() if lang else "FRANCE"
    return webhooks.get(lang_upper) or webhooks.get("FRANCE")

def send_discord_alert(webhook_url, title, store_name, product_url, image_url, price, stock, flag, lang="FRANCE"):
    if not webhook_url:
        return
    
    fields = [
        {"name": "💰 Prix", "value": price, "inline": True},
        {"name": "🏪 Boutique", "value": store_name, "inline": True},
        {"name": "🌍 Langue", "value": f"{flag} {lang}", "inline": True}
    ]
    
    if stock is not None:
        fields.append({"name": "📦 Stock", "value": str(stock), "inline": True})

    fields.append({"name": "🚀 Lien direct vers l'objet", "value": f"**[👉 CLIQUER ICI POUR OUVRIR L'ARTICLE]({product_url})**", "inline": False})

    embed = {
        "title": f"🔥 {title[:240]}",
        "url": product_url,
        "color": 16711680,
        "fields": fields
    }
    if image_url:
        embed["image"] = {"url": image_url}

    payload = {
        "username": "PokeSnipeBot",
        "embeds": [embed]
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        LOG.error(f"Erreur d'envoi Discord ({lang}) : {e}")

def run_check_cycle(args):
    LOG.info("Démarrage d'une vérification rapide en parallèle...")
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
        return

    shops = [s for s in shops_data if isinstance(s, dict) and s.get("enabled", True)]
    LOG.info(f"{len(shops)} boutique(s) active(s) en cours d'analyse simultanée...")

    seen_stocks = load_seen_stocks()
    new_seen_stocks = set(seen_stocks)
    alerts_sent = 0

    stock_file = Path(args.stock)
    stock_data = {"stores": {}}
    if stock_file.exists():
        try:
            with stock_file.open("r", encoding="utf-8") as f:
                stock_data = json.load(f)
        except Exception:
            pass

    if "stores" not in stock_data:
        stock_data["stores"] = {}

    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_store = {executor.submit(fetch_store_data, store): store for store in shops}
        for future in as_completed(future_to_store):
            try:
                store_obj, items = future.result()
                results.append((store_obj, items))
            except Exception:
                pass

    for store, items in results:
        store_name = store.get("name", "Inconnue")
        platform = store.get("platform", "").lower()
        
        if not items:
            continue
            
        store_key = f"{platform}-{store_name.lower().replace(' ', '-')}"
        stock_data["stores"][store_key] = {
            "name": store_name,
            "url": store.get("url", ""),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "source_status": f"{len(items)} produits lus",
            "products": []
        }
        
        for item in items:
            title = item.get("title", "")
            in_stock = item.get("in_stock", True)
            price = item.get("price", "N/C")
            stock = item.get("stock", None)
            img_url = item.get("image", "")
            product_url = item.get("url", store.get("url", ""))
            
            matched_product = match_product(title, products_config)
            
            if matched_product:
                stock_data["stores"][store_key]["products"].append({
                    "name": title,
                    "price": price,
                    "stock": stock,
                    "url": product_url,
                    "image": img_url,
                    "is_preorder": False,
                    "is_hot_series": False
                })
            
            if in_stock and matched_product:
                product_key = f"{store_name}:{title}"
                
                if product_key not in seen_stocks:
                    lang = matched_product.get("language", "FRANCE")
                    flag = matched_product.get("flag", "🇫🇷")
                    LOG.info(f"🚨 Alerte Pokémon [{lang}] ({price}) : {title} sur {store_name}")
                    alerts_sent += 1
                    
                    webhook_url = get_webhook_for_language(lang, webhooks)
                    if webhook_url:
                        send_discord_alert(webhook_url, title, store_name, product_url, img_url, price, stock, flag, lang)
                        time.sleep(1.2)
                
                new_seen_stocks.add(product_key)
                save_seen_stocks(new_seen_stocks)

    stock_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        stock_file.parent.mkdir(parents=True, exist_ok=True)
        with stock_file.open("w", encoding="utf-8") as f:
            json.dump(stock_data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    LOG.info(f"Vérification terminée. {alerts_sent} alerte(s) envoyée(s).")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bot de surveillance de restock Pokémon")
    parser.add_argument("--config", default="products.json")
    parser.add_argument("--shops", default="shops.json")
    parser.add_argument("--state", default="search_state.json")
    parser.add_argument("--stock", default="stock.json")
    parser.add_argument("--interval", type=int, default=60)
    
    args = parser.parse_args()
    LOG.info(f"Lancement du service multi-threadé en boucle ({args.interval}s)")
    
    while True:
        try:
            run_check_cycle(args)
        except Exception as e:
            LOG.error(f"Erreur critique : {e}")
        time.sleep(args.interval)