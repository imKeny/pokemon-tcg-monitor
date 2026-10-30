import urllib.request
import urllib.error
import json
import logging
import ssl

LOG = logging.getLogger("pokemon-restock")

def fetch_json_url(url, timeout=10):
    """Effectue une requête GET avec des headers de navigateur complets et SSL assoupli."""
    parsed = urllib.parse.urlparse(url)
    origin_url = f"{parsed.scheme}://{parsed.netloc}/"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": origin_url,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "X-Requested-With": "XMLHttpRequest"
    }
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
            if response.status == 200:
                return json.loads(response.read().decode("utf-8", errors="ignore"))
    except Exception as e:
        LOG.error(f"Erreur HTTP lors de l'accès à {url}: {e}")
    return None