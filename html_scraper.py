import urllib.request
import urllib.error
import logging
import ssl
import re
import html

LOG = logging.getLogger("pokemon-restock")

def fetch_philibert_catalog(store):
    """Récupère le catalogue HTML de Philibert avec debug renforcé."""
    store_url = store.get("url", "").rstrip("/")
    url = store.get("catalog_url") or f"{store_url}/fr/212-pokemon"
    name = store.get("name", "Philibert")
    
    LOG.info(f"Interrogation de la boutique spécifique {name} (HTML optimisé) : {url}")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9",
        "Referer": "https://www.philibertnet.com/"
    }
    
    items = []
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12, context=ctx) as response:
            if response.status == 200:
                html_content = response.read().decode("utf-8", errors="ignore")
                
                # Testons une regex beaucoup plus large sur les liens .html de produits PrestaShop
                # On cherche directement les balises de liens qui contiennent .html
                matches = re.findall(r'href="([^"]+\.html)"[^>]*>(.*?)<\/a>', html_content, re.DOTALL)
                
                seen_titles = set()
                for product_link, raw_title in matches:
                    clean_title = re.sub(r'<[^>]+>', '', raw_title).strip()
                    clean_title = html.unescape(clean_title)
                    
                    if clean_title and len(clean_title) > 4 and clean_title not in seen_titles:
                        if not any(bad in clean_title.lower() for bad in ["panier", "compte", "connexion", "accueil", "contact", "mentions", "politique"]):
                            seen_titles.add(clean_title)
                            items.append({
                                "title": clean_title,
                                "in_stock": True
                            })
                            
                if not items:
                    # Si toujours 0, sauvons un extrait pour voir si c'est Cloudflare
                    LOG.warning(f"Philibert a renvoyé du HTML mais aucun produit n'a matché. Longueur du HTML : {len(html_content)}")
                    if "cloudflare" in html_content.lower() or "challenge" in html_content.lower():
                        LOG.error("Philibert bloque les requêtes via une protection Cloudflare.")
                            
    except Exception as e:
        LOG.error(f"Erreur lors du scraping HTML de {name}: {e}")
        
    LOG.info(f"-> {len(items)} produits récupérés pour {name}.")
    return items

def fetch_generic_catalog(store):
    """Routeur générique pour le HTML selon la plateforme."""
    platform = store.get("platform")
    if platform == "philibert_html":
        return fetch_philibert_catalog(store)
    return []

def fetch_html_catalog(store):
    """Alias pour correspondre à l'appel principal."""
    return fetch_philibert_catalog(store)