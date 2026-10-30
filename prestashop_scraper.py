import logging
import urllib.parse
from http_client import fetch_json_url

LOG = logging.getLogger("pokemon-restock")

PRESTASHOP_SEARCH_TERMS = [
    "pokemon",
    "display",
    "etb",
    "coffret",
    "booster",
    "tripack",
    "duopack",
    "bundle",
    "pokebox",
    "tin",
    "blister"
]

def fetch_prestashop_catalog(store):
    """Récupère le catalogue d'une boutique PrestaShop (JSON) de manière robuste."""
    store_url = store.get("url", "").rstrip("/")
    if not store_url:
        return []
        
    search_terms = store.get("search_terms") or PRESTASHOP_SEARCH_TERMS
    all_items = []
    seen_titles = set()

    LOG.info(f"Récupération du catalogue PrestaShop pour {store.get('name', 'Boutique')}...")

    for term in search_terms:
        # URL de recherche standard PrestaShop compatible avec toutes les versions
        query_url = f"{store_url}/recherche?{urllib.parse.urlencode({'controller': 'search', 's': term})}"
        
        data = fetch_json_url(query_url, timeout=8)
        
        if data and isinstance(data, dict):
            products = data.get("products", [])
            for p in products:
                title = p.get("name", "").strip()
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    
                    # Détection précise du stock
                    in_stock = True
                    quantity = p.get("quantity")
                    if quantity is not None:
                        try:
                            in_stock = int(quantity) > 0
                        except (ValueError, TypeError):
                            pass
                    # Fallback sur l'attribut dispo si la quantité n'est pas explicite
                    elif "availability" in p:
                        avail = str(p.get("availability", "")).lower()
                        if "out" in avail or "rupture" in avail or "indisponible" in avail:
                            in_stock = False

                    all_items.append({
                        "title": title,
                        "in_stock": in_stock
                    })

    LOG.info(f"-> {len(all_items)} produits récupérés pour {store.get('name', 'Boutique')}.")
    return all_items