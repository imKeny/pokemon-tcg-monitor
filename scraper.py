import logging
from http_client import fetch_json_url

LOG = logging.getLogger("pokemon-restock")

def fetch_shopify_catalog(store):
    """Récupère les produits d'une boutique Shopify via son API JSON publique."""
    store_url = store.get("url", "").rstrip("/")
    if not store_url:
        return []
        
    # URL standard de l'API des produits Shopify
    catalog_url = f"{store_url}/products.json?limit=250"
    
    LOG.info(f"Récupération du catalogue Shopify pour {store.get('name', 'Boutique')}...")
    data = fetch_json_url(catalog_url)
    
    if data and isinstance(data, dict):
        products = data.get("products", [])
        items = []
        for p in products:
            title = p.get("title", "")
            variants = p.get("variants", [])
            # Vérifie si au moins un variant est disponible (en stock)
            in_stock = any(v.get("available", False) for v in variants)
            
            items.append({
                "title": title,
                "in_stock": in_stock
            })
        return items
        
    return []