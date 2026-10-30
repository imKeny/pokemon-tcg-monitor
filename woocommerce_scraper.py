import logging
from http_client import fetch_json_url

LOG = logging.getLogger("pokemon-restock")

def fetch_woocommerce_catalog(store):
    """Récupère les produits d'une boutique WooCommerce via la boutique ou l'API publique."""
    store_url = store.get("url", "").rstrip("/")
    if not store_url:
        return []
        
    # Route publique alternative ou flux RSS/JSON standard WooCommerce
    catalog_url = f"{store_url}/wp-json/wp/v2/posts" # ou catalogue public
    LOG.info(f"Récupération du catalogue WooCommerce pour {store.get('name', 'Boutique')}...")
    
    data = fetch_json_url(catalog_url)
    items = []
    if isinstance(data, list):
        for p in data:
            title = p.get("title", {}).get("rendered", "")
            items.append({"title": title, "in_stock": True})
            
    return items