#!/usr/bin/env python3
"""Monitor configured public product catalogs for Pokémon TCG stock."""

import argparse
import base64
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.utils import parsedate_to_datetime
import hashlib
from html.parser import HTMLParser
from html import unescape
import io
import json
import logging
import os
from pathlib import Path
import random
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen


LOG = logging.getLogger("pokemon-restock")
MIN_INTERVAL_SECONDS = 60
MAX_RESPONSE_BYTES = 5_000_000
EBAY_PRICE_CACHE_TTL_SECONDS = 24 * 60 * 60
EBAY_EMPTY_CACHE_TTL_SECONDS = 6 * 60 * 60
SHOPIFY_PRODUCT_LIMIT = 250
MAX_CATALOG_PAGES = 12
MIN_SHOPIFY_PRODUCT_LIMIT = 1
SEALED_PRODUCT_TERMS = (
    "booster",
    "display",
    "etb",
    "elite trainer",
    "dresseur d'elite",
    "dresseur elite",
    "coffret",
    "collection premium",
    "collection illustration",
    "pokebox",
    "mini tin",
    "tin",
    "blister",
    "bundle",
    "tripack",
    "duopack",
    "boite de boosters",
    "box de boosters",
    "collection box",
    "gift box",
    "anniversary box",
    "festival box",
    "special collection",
)
EXCLUDED_PRODUCT_TERMS = (
    "type:single",
    "carte a l'unite",
    "carte individuelle",
    "single card",
    "figurine",
    "peluche",
    "plush",
    "deckbox",
    "deck box",
    "classeur",
    "album",
    "pochette",
    "sleeve",
    "playmat",
    "tapis",
    "porte-cle",
    "porte cle",
    "accessoire",
    "goodies",
    "artset",
    "master set",
    "grade",
    "psa",
    "cgc",
    "protection",
    "phoenix shield",
)
PARKAGE_API = "https://back.parkage.com/api/parkage/search/autocomplete"
PARKAGE_IMAGE_BASE = "https://embedded.parkage.com/files/img/"
PARKAGE_SEARCH_TERMS = (
    "booster pokemon",
    "display pokemon",
    "boite de boosters pokemon",
    "etb pokemon",
    "dresseur d'elite pokemon",
    "coffret pokemon",
    "pokebox pokemon",
    "tin pokemon",
    "bundle pokemon",
    "blister pokemon",
    "tripack pokemon",
    "collection premium pokemon",
)
OUT_OF_STOCK_RETENTION_DAYS = 7
PARKAGE_LANGUAGES = {
    "jp": "Japonais",
    "kr": "Coréen",
    "cn": "Chinois",
    "tw": "Chinois",
    "en": "Anglais",
    "de": "Allemand",
}
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


PREORDER_TERMS = (
    "precommande",
    "pre commande",
    "preorder",
    "pre order",
    "a paraitre",
    "prochainement",
    "bientot disponible",
    "date de sortie",
)
HOT_SERIES_TERMS = (
    "upc",
    "ultra premium",
    "noctali",
    "noctalie",
    "mentali",
    "mentalie",
)


def normalize(text):
    text = text.replace("’", "'").replace("‘", "'")
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip()


def contains_product_term(text, term):
    term_pattern = re.escape(term).replace(r"\ ", r"\s+")
    return re.search(r"(?<!\w)" + term_pattern + r"s?(?!\w)", text) is not None


def detect_preorder(text):
    cleaned = re.sub(r"[-_/]+", " ", normalize(text))
    return any(contains_product_term(cleaned, term) for term in PREORDER_TERMS)


def detect_hot_series(text):
    cleaned = re.sub(r"[-_/]+", " ", normalize(text))
    return any(contains_product_term(cleaned, term) for term in HOT_SERIES_TERMS)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("products.json"))
    parser.add_argument("--shops", type=Path, default=Path("shops.json"))
    parser.add_argument("--state", type=Path, default=Path("search_state.json"))
    parser.add_argument("--stock", type=Path, default=Path("stock.json"))
    parser.add_argument(
        "--market-cache",
        type=Path,
        default=Path("market_price_cache.json"),
        help="cache for optional eBay market-price lookups",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="seconds between checks (default: 300; minimum: 60)",
    )
    parser.add_argument(
        "--test-discord",
        action="store_true",
        help="send one test message using DISCORD_WEBHOOK_URL from .env",
    )
    args = parser.parse_args()
    if args.interval < MIN_INTERVAL_SECONDS:
        parser.error(f"--interval must be at least {MIN_INTERVAL_SECONDS} seconds")
    return args


def load_local_environment(path):
    if not path.exists():
        return
    accepted_keys = {
        "DISCORD_WEBHOOK_URL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "SHOPIFY_STORES",
        "EBAY_CLIENT_ID",
        "EBAY_CLIENT_SECRET",
    }
    with path.open(encoding="utf-8") as env_file:
        for line_number, line in enumerate(env_file, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, separator, value = line.partition("=")
            if not separator or not key.strip():
                raise ValueError(f"Invalid environment entry in {path}:{line_number}")
            key = key.strip()
            if key not in accepted_keys:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ.setdefault(key, value)


def normalize_store_url(url, name):
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"{name} needs a non-empty Shopify store URL")
    parsed = urlparse(url.strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/", "/products.json")
    ):
        raise ValueError(
            f"{name} must be an HTTPS store root or /products.json URL"
        )
    hostname = parsed.hostname.lower()
    if "." not in hostname or hostname.endswith((".local", ".internal")):
        raise ValueError(f"{name} must use a public store domain")
    return urlunparse(("https", hostname, "", "", "", ""))


def validate_catalog_urls(catalog_urls, hostname, store_name):
    if not isinstance(catalog_urls, list) or not catalog_urls:
        raise ValueError(f"{store_name} needs at least one catalog URL")
    if len(catalog_urls) > 20:
        raise ValueError(f"{store_name} can configure at most 20 catalog URLs")
    validated_urls = []
    for catalog_url in catalog_urls:
        if not isinstance(catalog_url, str) or not catalog_url.strip():
            raise ValueError(f"{store_name} catalog URLs must be non-empty strings")
        parsed_url = urlparse(catalog_url.strip())
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname != hostname
            or parsed_url.port not in (None, 443)
            or parsed_url.username
            or parsed_url.password
            or parsed_url.fragment
            or not parsed_url.path.startswith("/")
        ):
            raise ValueError(
                f"{store_name} catalog URLs must use HTTPS on the configured "
                "store host"
            )
        validated_urls.append(catalog_url.strip())
    return validated_urls


def validate_collections(collections, label):
    if collections is None:
        return []
    if (
        not isinstance(collections, list)
        or len(collections) > 20
        or any(
            not isinstance(handle, str)
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", handle)
            for handle in collections
        )
    ):
        raise ValueError(
            f"{label} collections must be a list of up to 20 Shopify collection handles"
        )
    return collections


def load_stores(path):
    with path.open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    if not isinstance(config, dict):
        raise ValueError(f"{path} must contain a JSON object")
    configured_stores = config.get("stores", config.get("shopify_stores", []))
    if not isinstance(configured_stores, list):
        raise ValueError("'stores' must be a list")
    retail_prices = config.get("retail_prices", [])
    if not isinstance(retail_prices, list):
        raise ValueError("'retail_prices' must be a list")
    retail_estimates = config.get("retail_estimates", [])
    if not isinstance(retail_estimates, list):
        raise ValueError("'retail_estimates' must be a list")

    stores = {}
    for index, store in enumerate(configured_stores, start=1):
        if not isinstance(store, dict):
            raise ValueError(f"Store #{index} must be a JSON object")
        name = store.get("name", f"Store #{index}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Store #{index} needs a non-empty name")
        platform = store.get("platform", "shopify")
        if platform not in (
            "shopify",
            "woocommerce",
            "philibert_html",
            "ultrajeux_html",
            "parkage_api",
        ):
            raise ValueError(
                f"Store #{index} platform must be 'shopify', 'woocommerce', "
                "'philibert_html', 'ultrajeux_html', or 'parkage_api'"
            )
        url = normalize_store_url(store.get("url"), f"Store #{index}")
        hostname = urlparse(url).hostname
        catalog_url = store.get("catalog_url")
        catalog_urls = store.get("catalog_urls", [])
        if platform == "philibert_html":
            catalog_url = catalog_url or urljoin(url + "/", "fr/212-pokemon")
            parsed_catalog_url = urlparse(catalog_url)
            if (
                parsed_catalog_url.scheme != "https"
                or parsed_catalog_url.hostname != hostname
                or parsed_catalog_url.username
                or parsed_catalog_url.password
                or parsed_catalog_url.fragment
            ):
                raise ValueError(
                    f"Store #{index} catalog_url must be an HTTPS URL "
                    "on the configured store host"
                )
        elif platform == "ultrajeux_html":
            catalog_urls = validate_catalog_urls(
                catalog_urls, hostname, f"Store #{index}"
            )
            if catalog_url is not None:
                raise ValueError(
                    f"Store #{index} must use catalog_urls for ultrajeux_html"
                )
        elif catalog_urls:
            raise ValueError(
                f"Store #{index} catalog_urls are only supported for "
                "ultrajeux_html"
            )
        elif catalog_url is not None:
            raise ValueError(
                f"Store #{index} catalog_url is only supported for philibert_html"
            )
        collection = store.get("collection")
        category_ids = store.get("product_category_ids", [])
        if platform == "shopify" and category_ids:
            raise ValueError(
                f"Store #{index} product_category_ids are only supported "
                "for WooCommerce"
            )
        if collection is not None and (
            not isinstance(collection, str)
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", collection)
        ):
            raise ValueError(
                f"Shopify store #{index} collection must be a lowercase "
                "Shopify collection handle"
            )
        if (
            not isinstance(category_ids, list)
            or any(
                not isinstance(category_id, int)
                or isinstance(category_id, bool)
                or category_id <= 0
                for category_id in category_ids
            )
        ):
            raise ValueError(
                f"Store #{index} product_category_ids must be a list of "
                "positive integers"
            )
        store_id = f"{platform}-{hostname}"
        if collection:
            if platform != "shopify":
                raise ValueError(
                    f"Store #{index} collection is only supported for Shopify"
                )
            store_id = f"{store_id}-collection-{collection}"
        stores[hostname] = {
            "id": store_id,
            "name": name.strip(),
            "url": url,
            "platform": platform,
            "collection": collection,
            "collections": validate_collections(
                store.get("collections"), f"Store #{index}"
            ),
            "catalog_url": catalog_url,
            "catalog_urls": catalog_urls,
            "product_category_ids": category_ids,
        }
        product_limit = store.get("product_limit", SHOPIFY_PRODUCT_LIMIT)
        if (
            not isinstance(product_limit, int)
            or isinstance(product_limit, bool)
            or not MIN_SHOPIFY_PRODUCT_LIMIT <= product_limit <= SHOPIFY_PRODUCT_LIMIT
        ):
            raise ValueError(
                f"Store #{index} product_limit must be between "
                f"{MIN_SHOPIFY_PRODUCT_LIMIT} and {SHOPIFY_PRODUCT_LIMIT}"
            )
        stores[hostname]["product_limit"] = product_limit

    env_stores = os.environ.get("SHOPIFY_STORES", "")
    for index, entry in enumerate(re.split(r"[,;\n]", env_stores), start=1):
        entry = entry.strip()
        if not entry:
            continue
        url = normalize_store_url(entry, f"SHOPIFY_STORES entry #{index}")
        hostname = urlparse(url).hostname
        stores.setdefault(
            hostname,
            {
                "id": f"shopify-{hostname}",
                "name": hostname,
                "url": url,
                "collection": None,
                "platform": "shopify",
                "catalog_url": None,
                "catalog_urls": [],
                "product_category_ids": [],
                "product_limit": SHOPIFY_PRODUCT_LIMIT,
            },
        )

    for store in stores.values():
        store["retail_prices"] = {}
        store["retail_estimates"] = {}
    seen_retail_prices = set()
    for index, reference in enumerate(retail_prices, start=1):
        if not isinstance(reference, dict):
            raise ValueError(f"Retail price #{index} must be a JSON object")
        store_host = reference.get("store")
        handle = reference.get("handle")
        price_value = reference.get("price")
        if not isinstance(store_host, str) or store_host.lower() not in stores:
            raise ValueError(
                f"Retail price #{index} store must match a configured store hostname"
            )
        store_host = store_host.lower()
        if not isinstance(handle, str) or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", handle
        ):
            raise ValueError(f"Retail price #{index} has an invalid product handle")
        try:
            retail_price = Decimal(str(price_value))
        except (InvalidOperation, ValueError):
            raise ValueError(f"Retail price #{index} has an invalid price") from None
        if not retail_price.is_finite() or retail_price <= 0:
            raise ValueError(f"Retail price #{index} must be greater than zero")
        key = (store_host, handle)
        if key in seen_retail_prices:
            raise ValueError(
                f"Duplicate retail price for {store_host}/products/{handle}"
            )
        seen_retail_prices.add(key)
        stores[store_host]["retail_prices"][handle] = retail_price

    seen_estimate_categories = set()
    for index, estimate in enumerate(retail_estimates, start=1):
        if not isinstance(estimate, dict):
            raise ValueError(f"Retail estimate #{index} must be a JSON object")
        category = estimate.get("category")
        label = estimate.get("label")
        if (
            not isinstance(category, str)
            or not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", category)
        ):
            raise ValueError(f"Retail estimate #{index} has an invalid category")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"Retail estimate #{index} needs a non-empty label")
        if category in seen_estimate_categories:
            raise ValueError(f"Duplicate retail estimate for {category}")
        try:
            minimum = Decimal(str(estimate.get("minimum", "")))
            maximum = Decimal(str(estimate.get("maximum", "")))
        except (InvalidOperation, ValueError):
            raise ValueError(
                f"Retail estimate #{index} has an invalid price range"
            ) from None
        if (
            not minimum.is_finite()
            or not maximum.is_finite()
            or minimum <= 0
            or maximum < minimum
        ):
            raise ValueError(
                f"Retail estimate #{index} must have positive prices and "
                "maximum >= minimum"
            )
        seen_estimate_categories.add(category)
        for store in stores.values():
            store["retail_estimates"][category] = {
                "label": label.strip(),
                "minimum": minimum,
                "maximum": maximum,
            }

    if not stores:
        raise ValueError(
            "No product catalogs configured. Add Shopify URLs to SHOPIFY_STORES "
            "in .env or store entries to 'stores' in products.json"
        )
    return list(stores.values())


def load_affiliate_feed_stores(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    if not isinstance(config, dict) or not isinstance(config.get("shops"), list):
        raise ValueError(f"{path} must contain a 'shops' list")

    stores = {}
    for index, shop in enumerate(config["shops"], start=1):
        if not isinstance(shop, dict):
            raise ValueError(f"Shop #{index} must be a JSON object")
        if shop.get("enabled", True) is False:
            continue
        name = shop.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Shop #{index} needs a non-empty name")
        url = normalize_store_url(shop.get("url"), f"Shop #{index}")
        hostname = urlparse(url).hostname
        feed_url = shop.get("feed_url")
        if feed_url is not None:
            if not isinstance(feed_url, str) or not feed_url.strip():
                raise ValueError(f"Shop #{index} feed_url must be a non-empty URL")
            parsed_feed_url = urlparse(feed_url.strip())
            if (
                parsed_feed_url.scheme != "https"
                or not parsed_feed_url.hostname
                or parsed_feed_url.username
                or parsed_feed_url.password
                or parsed_feed_url.fragment
                or parsed_feed_url.port not in (None, 443)
            ):
                raise ValueError(
                    f"Shop #{index} feed_url must be a public HTTPS URL"
                )
            feed_url = feed_url.strip()
        feed_format = shop.get("feed_format", "auto")
        if feed_format not in ("auto", "xml", "csv"):
            raise ValueError(
                f"Shop #{index} feed_format must be 'auto', 'xml', or 'csv'"
            )
        platform = shop.get("platform", "auto")
        if platform not in (
            "auto",
            "shopify",
            "woocommerce",
            "philibert_html",
            "ultrajeux_html",
            "parkage_api",
            "prestashop_json",
        ):
            raise ValueError(
                f"Shop #{index} platform must be 'auto', 'shopify', "
                "'woocommerce', 'philibert_html', 'ultrajeux_html', "
                "'parkage_api', or 'prestashop_json'"
            )
        search_terms = shop.get("search_terms", [])
        categories = shop.get("categories", [])
        if (
            not isinstance(search_terms, list)
            or len(search_terms) > 30
            or any(
                not isinstance(term, str) or not 2 <= len(term.strip()) <= 60
                for term in search_terms
            )
            or not isinstance(categories, list)
            or len(categories) > 30
            or any(
                not isinstance(category, str)
                or not re.fullmatch(r"[0-9]+-[a-z0-9-]+", category)
                for category in categories
            )
        ):
            raise ValueError(
                f"Shop #{index} search_terms must be short strings and "
                "categories PrestaShop slugs like '2060-nouveautes'"
            )
        collection = shop.get("collection")
        if collection is not None and (
            platform != "shopify"
            or not isinstance(collection, str)
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", collection)
        ):
            raise ValueError(
                f"Shop #{index} collection must be a Shopify collection handle "
                "and requires platform 'shopify'"
            )
        catalog_urls = shop.get("catalog_urls", [])
        if platform == "ultrajeux_html":
            catalog_urls = validate_catalog_urls(
                catalog_urls, hostname, f"Shop #{index}"
            )
        elif catalog_urls:
            raise ValueError(
                f"Shop #{index} catalog_urls are only supported for "
                "ultrajeux_html"
            )
        product_limit = shop.get("product_limit", SHOPIFY_PRODUCT_LIMIT)
        if (
            not isinstance(product_limit, int)
            or isinstance(product_limit, bool)
            or not MIN_SHOPIFY_PRODUCT_LIMIT
            <= product_limit
            <= SHOPIFY_PRODUCT_LIMIT
        ):
            raise ValueError(
                f"Shop #{index} product_limit must be between "
                f"{MIN_SHOPIFY_PRODUCT_LIMIT} and {SHOPIFY_PRODUCT_LIMIT}"
            )
        if hostname in stores:
            raise ValueError(f"Duplicate shop URL hostname: {hostname}")
        stores[hostname] = {
            "id": f"auto-{hostname}",
            "name": name.strip(),
            "url": url,
            "platform": platform,
            "collection": collection,
            "collections": validate_collections(
                shop.get("collections"), f"Shop #{index}"
            ),
            "search_terms": [term.strip() for term in search_terms],
            "categories": categories,
            "catalog_urls": catalog_urls,
            "feed_url": feed_url,
            "feed_format": None if feed_format == "auto" else feed_format,
            "source_mode": "auto",
            "product_limit": product_limit,
            "retail_prices": {},
            "retail_estimates": {},
        }
    return list(stores.values())


def load_state(path):
    if not path.exists():
        return {"searches": {}}
    with path.open(encoding="utf-8") as state_file:
        state = json.load(state_file)
    if not isinstance(state, dict) or not isinstance(state.get("searches", {}), dict):
        raise ValueError(f"{path} contains invalid stock state data")
    for store_state in state.get("searches", {}).values():
        if (
            not isinstance(store_state, dict)
            or not isinstance(store_state.get("products", {}), dict)
            or not all(
                isinstance(url, str)
                and isinstance(product, dict)
                and isinstance(product.get("name"), str)
                for url, product in store_state.get("products", {}).items()
            )
        ):
            raise ValueError(f"{path} contains invalid store product data")
    state.setdefault("searches", {})
    return state


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as state_file:
        json.dump(state, state_file, ensure_ascii=False, indent=2)
        state_file.write("\n")
    temporary_path.replace(path)


def load_stock_snapshot(path):
    if not path.exists():
        return {"updated_at": None, "stores": {}}
    with path.open(encoding="utf-8") as stock_file:
        stock = json.load(stock_file)
    if (
        not isinstance(stock, dict)
        or not isinstance(stock.get("stores", {}), dict)
    ):
        raise ValueError(f"{path} contains invalid dashboard stock data")
    return stock


def save_stock_snapshot(path, stock):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as stock_file:
        json.dump(stock, stock_file, ensure_ascii=False, indent=2)
        stock_file.write("\n")
    temporary_path.replace(path)


def save_auto_discovery_status(stock, store, status):
    previous_snapshot = stock["stores"].get(store["id"], {})
    stock["stores"][store["id"]] = {
        "name": store["name"],
        "url": store["url"],
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_status": status,
        "products": previous_snapshot.get("products", []),
    }


def fetch_json(url):
    parsed = urlparse(url)
    origin_url = f"{parsed.scheme}://{parsed.netloc}/"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": origin_url,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=25) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError(f"JSON response exceeded {MAX_RESPONSE_BYTES} bytes")
            return json.loads(body)
    except HTTPError as error:
        if error.code in (429, 503):
            LOG.warning(
                "Shopify endpoint returned HTTP %s; Retry-After=%s. "
                "Stopping this check cycle.",
                error.code,
                error.headers.get("Retry-After", "not supplied"),
            )
        else:
            LOG.error("HTTP %s while checking Shopify endpoint %s", error.code, url)
        raise
    except URLError:
        LOG.warning("Network error while checking Shopify endpoint %s", url)
        raise
    except json.JSONDecodeError as error:
        raise ValueError(f"Shopify endpoint returned invalid JSON: {url}") from error


def fetch_shopify_catalog(store):
    collections = store.get("collections") or [store.get("collection")]
    page_size = SHOPIFY_PRODUCT_LIMIT
    max_pages = store.get("max_pages", MAX_CATALOG_PAGES)
    while True:
        all_products = {}
        try:
            for collection in collections:
                base = store["url"]
                if collection:
                    base += f"/collections/{collection}"
                for page in range(1, max_pages + 1):
                    if page > 1 or all_products:
                        time.sleep(random.uniform(0.6, 1.2))
                    payload = fetch_json(
                        f"{base}/products.json?{urlencode({'limit': page_size, 'page': page})}"
                    )
                    page_products = (
                        payload.get("products") if isinstance(payload, dict) else None
                    )
                    if not isinstance(page_products, list):
                        raise ValueError(
                            f"{store['name']} response has no Shopify products list"
                        )
                    for product in page_products:
                        if isinstance(product, dict):
                            all_products[product.get("id") or product.get("handle")] = product
                    if len(page_products) < page_size:
                        break
        except ValueError as error:
            # Pages trop lourdes (> limite de réponse) : on recommence avec des pages plus petites.
            if "exceeded" in str(error) and page_size > 50:
                page_size = 50
                continue
            raise
        break
    products, recognized_count = parse_shopify_products(
        {"products": list(all_products.values())}, store
    )
    store["source_status"] = (
        f"Catalogue Shopify public ({len(all_products)} produits lus, "
        f"{recognized_count} scellés)"
    )
    return products, recognized_count


PRESTASHOP_SEARCH_TERMS = (
    "display pokemon",
    "etb pokemon",
    "coffret pokemon",
    "booster pokemon",
    "tripack pokemon",
    "duopack pokemon",
    "bundle pokemon",
    "pokebox",
    "tin pokemon",
    "blister pokemon",
)


def _prestashop_image(product):
    cover = product.get("cover")
    if not isinstance(cover, dict):
        return None
    sizes = cover.get("bySize") if isinstance(cover.get("bySize"), dict) else {}
    for key in ("large_default", "medium_default", "default_md"):
        entry = sizes.get(key)
        if isinstance(entry, dict) and isinstance(entry.get("url"), str):
            return entry["url"]
    large = cover.get("large")
    return large.get("url") if isinstance(large, dict) else None


def fetch_prestashop_catalog(store):
    queries = [
        (f"{store['url']}/recherche?" + urlencode({"controller": "search", "s": term}))
        for term in (store.get("search_terms") or PRESTASHOP_SEARCH_TERMS)
    ] + [f"{store['url']}/{category}" for category in store.get("categories", [])]
    page_size = 50
    max_pages = store.get("max_pages", MAX_CATALOG_PAGES)
    normalized_products = {}
    request_count = 0
    for query_url in queries:
        for page in range(1, max_pages + 1):
            if request_count:
                time.sleep(random.uniform(0.6, 1.2))
            request_count += 1
            separator = "&" if "?" in query_url else "?"
            body, charset = _read_public_response(
                f"{query_url}{separator}{urlencode({'resultsPerPage': page_size, 'page': page})}",
                accept="application/json",
                store=store,
                extra_headers={"X-Requested-With": "XMLHttpRequest"},
            )
            try:
                payload = json.loads(body.decode(charset, errors="replace"))
            except json.JSONDecodeError as error:
                raise ValueError(f"{store['name']} listing is not valid JSON") from error
            products = payload.get("products") if isinstance(payload, dict) else None
            if not isinstance(products, list):
                raise ValueError(f"{store['name']} response has no products list")
            for product in products:
                if not isinstance(product, dict) or not isinstance(product.get("name"), str):
                    continue
                product_url = product.get("canonical_url") or product.get("url")
                if not isinstance(product_url, str):
                    continue
                image = _prestashop_image(product)
                description = re.sub(r"<[^>]+>", " ", str(product.get("description_short") or ""))
                normalized_products[product.get("id_product") or product_url] = {
                    "title": unescape(product["name"]),
                    "handle": None,
                    "url": product_url,
                    "product_type": str(product.get("category_name") or ""),
                    "vendor": str(product.get("manufacturer_name") or ""),
                    "tags": (
                        ["Précommande"]
                        if detect_preorder(f"{product['name']} {description}")
                        else []
                    ),
                    "images": [{"src": image, "alt": product["name"]}] if image else [],
                    "variants": [
                        {
                            "available": bool(product.get("add_to_cart_url")),
                            "price": product.get("price_amount"),
                        }
                    ],
                }
            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            pages_count = pagination.get("pages_count") if isinstance(pagination, dict) else 1
            if not isinstance(pages_count, int) or page >= pages_count:
                break
    parsed, recognized_count = parse_shopify_products(
        {"products": list(normalized_products.values())}, store
    )
    store["source_status"] = (
        f"Catalogue PrestaShop public ({len(normalized_products)} produits lus, "
        f"{recognized_count} scellés)"
    )
    return parsed, recognized_count


def fetch_woocommerce_products(store):
    endpoint = (
        f"{store['url']}/wp-json/wc/store/v1/products"
    )
    products = []
    page_size = 100
    max_products = page_size * MAX_CATALOG_PAGES
    page = 1
    while len(products) < max_products:
        if page > 1:
            time.sleep(random.uniform(0.6, 1.2))
        params = {
            "per_page": page_size,
            "page": page,
        }
        category_ids = store.get("product_category_ids", [])
        if category_ids:
            params["category"] = ",".join(str(value) for value in category_ids)
        payload = fetch_json(f"{endpoint}?{urlencode(params)}")
        if not isinstance(payload, list):
            raise ValueError(
                f"{store['name']} WooCommerce Store API response must be a list"
            )
        products.extend(payload)
        if len(payload) < params["per_page"]:
            break
        page += 1
    return parse_woocommerce_products(products[:max_products], store)


def parse_woocommerce_products(payload, store):
    normalized_products = []
    for product in payload:
        if not isinstance(product, dict):
            continue
        prices = product.get("prices")
        price_value = None
        if (
            isinstance(prices, dict)
            and prices.get("currency_code") == "EUR"
            and isinstance(prices.get("price"), str)
        ):
            try:
                minor_unit = prices.get("currency_minor_unit", 2)
                if (
                    isinstance(minor_unit, int)
                    and not isinstance(minor_unit, bool)
                    and 0 <= minor_unit <= 6
                ):
                    price_value = str(
                        Decimal(prices["price"])
                        / (Decimal(10) ** minor_unit)
                    )
            except (InvalidOperation, ValueError):
                price_value = None

        categories = product.get("categories", [])
        tags = product.get("tags", [])
        category_names = [
            item.get("name", "")
            for item in categories
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ] if isinstance(categories, list) else []
        tag_names = [
            item.get("name", "")
            for item in tags
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ] if isinstance(tags, list) else []
        images = product.get("images", [])
        if not isinstance(images, list):
            images = []
        normalized_products.append(
            {
                "title": unescape(str(product.get("name", ""))),
                "handle": product.get("slug"),
                "url": product.get("permalink"),
                "product_type": " ".join(category_names),
                "tags": tag_names,
                "images": [
                    {
                        "src": image.get("src"),
                        "alt": unescape(str(image.get("alt", ""))),
                    }
                    for image in images
                    if isinstance(image, dict)
                ],
                "variants": [
                    {
                        "available": product.get("is_in_stock") is True,
                        "price": price_value,
                    }
                ],
            }
        )
    return parse_shopify_products({"products": normalized_products}, store)


class PhilibertCatalogParser(HTMLParser):
    VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.card = None
        self.cards = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.stack.append(tag)
        depth = len(self.stack)
        classes = attributes.get("class", "").split()
        if self.card is None and tag == "div" and "product-card--full" in classes:
            self.card = {
                "depth": depth,
                "title_depth": None,
                "price_depth": None,
                "title": [],
                "price": [],
                "text": [],
                "url": None,
                "image": None,
                "pid": attributes.get("data-pid"),
            }
        if self.card is None:
            return
        if tag == "a" and "product-card__title" in classes:
            self.card["title_depth"] = depth
            self.card["url"] = attributes.get("href")
        if "product-card__price" in classes:
            self.card["price_depth"] = depth
        if tag == "img" and self.card["image"] is None:
            self.card["image"] = (
                attributes.get("src")
                or attributes.get("data-src")
                or attributes.get("data-original")
            )
        if tag in self.VOID_TAGS:
            self.stack.pop()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.card is None:
            return
        text = " ".join(data.split())
        if not text:
            return
        self.card["text"].append(text)
        depth = len(self.stack)
        if self.card["title_depth"] is not None and depth >= self.card["title_depth"]:
            self.card["title"].append(text)
        if self.card["price_depth"] is not None and depth >= self.card["price_depth"]:
            self.card["price"].append(text)

    def handle_endtag(self, tag):
        matching_index = next(
            (
                index
                for index in range(len(self.stack) - 1, -1, -1)
                if self.stack[index] == tag
            ),
            None,
        )
        if matching_index is None:
            return
        closing_depth = matching_index + 1
        if self.card is not None:
            if self.card["title_depth"] == closing_depth:
                self.card["title_depth"] = None
            if self.card["price_depth"] == closing_depth:
                self.card["price_depth"] = None
            if self.card["depth"] == closing_depth:
                self.cards.append(self.card)
                self.card = None
        self.stack = self.stack[:matching_index]


def parse_philibert_products(document, store, stock_by_pid=None):
    parser = PhilibertCatalogParser()
    parser.feed(document)
    normalized_products = []
    for card in parser.cards[: store["product_limit"]]:
        title = unescape(" ".join(card["title"])).strip()
        product_url = urljoin(store["url"] + "/", card["url"] or "")
        parsed_url = urlparse(product_url)
        price_text = " ".join(card["price"])
        match = re.search(
            r"(?P<amount>(?:\d{1,3}(?:[ .]\d{3})+|\d+)[,.]\d{2})\s*(?:€|&euro;|eur)",
            price_text,
            re.IGNORECASE,
        )
        normalized_text = normalize(" ".join(card["text"]))
        is_preorder = "dispo :" in normalized_text
        if not title or parsed_url.hostname != urlparse(store["url"]).hostname:
            continue
        price = parse_euro_price(match.group("amount")) if match else None
        # Une précommande sans prix publié reste suivie, avec un prix « à venir ».
        if price is None and not is_preorder:
            continue
        if stock_by_pid is not None and card.get("pid"):
            is_available = stock_by_pid.get(card["pid"]) is True
        else:
            is_available = not any(
                term in normalized_text
                for term in ("rupture de stock", "epuise")
            )
        if is_preorder:
            is_available = True
        image = urljoin(store["url"], card["image"]) if card["image"] else None
        normalized_products.append(
            {
                "title": title,
                "handle": None,
                "url": product_url,
                "product_type": "",
                "tags": ["Précommande"] if is_preorder else [],
                "images": [{"src": image, "alt": title}] if image else [],
                "variants": [
                    {
                        "available": is_available,
                        "price": str(price) if price is not None else None,
                    }
                ],
            }
        )
    return parse_shopify_products({"products": normalized_products}, store)


def fetch_philibert_stock(store, product_ids):
    stocks = {}
    for start in range(0, len(product_ids), 40):
        chunk = product_ids[start : start + 40]
        request = Request(
            f"{store['url']}/fr/ajax/stock/{','.join(chunk)}",
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": store["catalog_url"],
                "User-Agent": BROWSER_USER_AGENT,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        with urlopen(request, timeout=20) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            raise ValueError(f"{store['name']} stock endpoint returned invalid JSON") from error
        chunk_stocks = payload.get("stocks") if isinstance(payload, dict) else None
        if not isinstance(chunk_stocks, dict):
            raise ValueError(f"{store['name']} stock endpoint response has no stocks")
        for pid, value in chunk_stocks.items():
            stocks[str(pid)] = value is True or (
                isinstance(value, int) and not isinstance(value, bool) and value > 0
            )
    return stocks


def fetch_philibert_catalog(store):
    request = Request(
        store["catalog_url"],
        headers={
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Upgrade-Insecure-Requests": "1",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            charset = response.headers.get_content_charset() or "utf-8"
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError(
                f"{store['name']} HTML response exceeded {MAX_RESPONSE_BYTES} bytes"
            )
        document = body.decode(charset, errors="replace")
    except HTTPError:
        raise
    except URLError:
        LOG.warning("%s: network error while reading public catalog", store["name"])
        raise
    # La fiche liste charge le stock via /fr/ajax/stock/<ids>, absent du HTML statique.
    parser = PhilibertCatalogParser()
    parser.feed(document)
    product_ids = [card["pid"] for card in parser.cards if card.get("pid")]
    stock_by_pid = fetch_philibert_stock(store, product_ids) if product_ids else None
    products, recognized_count = parse_philibert_products(document, store, stock_by_pid)
    store["source_status"] = f"Catalogue Philibert + stock public ({recognized_count} produits scellés)"
    return products, recognized_count


class UltraJeuxCatalogParser(HTMLParser):
    VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.card = None
        self.cards = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.stack.append(tag)
        depth = len(self.stack)
        classes = attributes.get("class", "").split()
        if self.card is None and tag == "div" and "block_produit" in classes:
            self.card = {
                "depth": depth,
                "label_depth": None,
                "title_depth": None,
                "price_depth": None,
                "availability_depth": None,
                "label": [],
                "title": [],
                "price": [],
                "availability": [],
                "text": [],
                "url": None,
                "link_title": "",
                "images": [],
            }
        if self.card is not None:
            if "main_bloc_article_titre" in classes:
                self.card["label_depth"] = depth
            if tag == "p" and "titre" in classes:
                self.card["title_depth"] = depth
            if tag == "p" and "prix" in classes:
                self.card["price_depth"] = depth
            if tag == "p" and "disponibilite" in classes:
                self.card["availability_depth"] = depth
            if tag == "a":
                href = attributes.get("href", "")
                if urlparse(href).path.rsplit("/", 1)[-1].startswith("produit-"):
                    self.card["url"] = href
                    self.card["link_title"] = attributes.get("title", "")
            if tag == "img":
                self.card["images"].append(attributes)
        if tag in self.VOID_TAGS:
            self.stack.pop()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.card is None:
            return
        text = " ".join(data.split())
        if not text:
            return
        self.card["text"].append(text)
        depth = len(self.stack)
        for field in ("label", "title", "price", "availability"):
            field_depth = self.card[f"{field}_depth"]
            if field_depth is not None and depth >= field_depth:
                self.card[field].append(text)

    def handle_endtag(self, tag):
        matching_index = next(
            (
                index
                for index in range(len(self.stack) - 1, -1, -1)
                if self.stack[index] == tag
            ),
            None,
        )
        if matching_index is None:
            return
        closing_depth = matching_index + 1
        if self.card is not None:
            for field in ("label", "title", "price", "availability"):
                depth_key = f"{field}_depth"
                if self.card[depth_key] == closing_depth:
                    self.card[depth_key] = None
            if self.card["depth"] == closing_depth:
                self.cards.append(self.card)
                self.card = None
        self.stack = self.stack[:matching_index]


def _ultrajeux_product_image(image_candidates):
    best_image = None
    best_score = 0
    for image in image_candidates:
        src = (
            image.get("src")
            or image.get("data-src")
            or image.get("data-original")
        )
        if not isinstance(src, str) or not src.strip():
            continue
        normalized_src = src.casefold()
        if any(term in normalized_src for term in ("icon_", "/pays/", "logo", "flag")):
            continue
        classes = (image.get("class") or "").casefold()
        score = 0
        if any(term in classes for term in ("produit", "article", "scan")):
            score += 4
        if re.search(r"\.(?:jpe?g|webp)(?:[?#]|$)", normalized_src):
            score += 3
        if (image.get("alt") or "").strip():
            score += 1
        if score > best_score:
            best_image = src
            best_score = score
    return best_image


def _extract_ultrajeux_products(document, store):
    parser = UltraJeuxCatalogParser()
    parser.feed(document)
    normalized_products = []
    store_host = urlparse(store["url"]).hostname
    for card in parser.cards[: store["product_limit"]]:
        title = " ".join(card["title"]).strip() or str(
            card["link_title"] or ""
        ).strip()
        product_url = urljoin(store["url"] + "/", card["url"] or "")
        parsed_url = urlparse(product_url)
        if (
            not title
            or not card["url"]
            or parsed_url.scheme != "https"
            or parsed_url.hostname != store_host
        ):
            continue
        price_text = " ".join(card["price"])
        match = re.search(
            r"(?P<amount>(?:\d{1,3}(?:[ .]\d{3})+|\d+)[,.]\d{2})\s*(?:€|&euro;|eur)",
            price_text,
            re.IGNORECASE,
        )
        if not match:
            continue
        price = parse_euro_price(match.group("amount"))
        if price is None:
            continue
        availability = normalize(" ".join(card["availability"]))
        is_preorder = detect_preorder(availability)
        is_available = is_preorder or (
            any(
                term in availability
                for term in ("disponible", "en stock", "ajouter au panier")
            )
            and not any(
                term in availability
                for term in (
                    "indisponible",
                    "rupture",
                    "epuise",
                    "en magasin",
                )
            )
        )
        image = _ultrajeux_product_image(card["images"])
        if image:
            image = urljoin(store["url"] + "/", image)
        normalized_products.append(
            {
                "title": title,
                "handle": None,
                "url": product_url,
                "product_type": " ".join(card["label"]).strip(),
                "tags": ["Précommande"] if is_preorder else [],
                "images": [{"src": image, "alt": title}] if image else [],
                "variants": [{"available": is_available, "price": str(price)}],
            }
        )
    return normalized_products


def parse_ultrajeux_products(document, store):
    normalized_products = _extract_ultrajeux_products(document, store)
    return parse_shopify_products({"products": normalized_products}, store)


def fetch_ultrajeux_catalog(store):
    normalized_products = {}
    for index, catalog_url in enumerate(store["catalog_urls"]):
        if index:
            time.sleep(random.uniform(2, 4))
        body, charset = _read_public_response(
            catalog_url,
            accept="text/html,application/xhtml+xml",
            store=store,
        )
        for product in _extract_ultrajeux_products(
            body.decode(charset, errors="replace"), store
        ):
            normalized_products[product["url"]] = product
    products, recognized_count = parse_shopify_products(
        {"products": list(normalized_products.values())},
        store,
    )
    store["source_status"] = (
        f"Catalogue HTML public Ultra-Jeux ({recognized_count} produits scellés)"
    )
    return products, recognized_count


def fetch_parkage_catalog(store):
    normalized_products = {}
    for index, term in enumerate(PARKAGE_SEARCH_TERMS):
        if index:
            time.sleep(random.uniform(1, 2))
        request = Request(
            f"{PARKAGE_API}?{urlencode({'search': term})}",
            headers={
                "Accept": "application/json",
                "Accept-Language": "fr-FR,fr;q=0.9",
                "Origin": store["url"],
                "Referer": store["url"] + "/",
                "User-Agent": BROWSER_USER_AGENT,
            },
        )
        with urlopen(request, timeout=25) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError(f"{store['name']} API response too large")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            raise ValueError(f"{store['name']} API returned invalid JSON") from error
        records = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            continue
        for record in records:
            if (
                not isinstance(record, dict)
                or record.get("deleted")
                or record.get("visibility") != 1
                or record.get("type") != "sealed"
                or not isinstance(record.get("id"), int)
            ):
                continue
            title = record.get("name_fr") or record.get("name_en")
            price = record.get("price_discount") or record.get("price")
            if not isinstance(title, str) or not isinstance(price, (int, float)):
                continue
            stock = record.get("stock")
            preorder_date = record.get("preorder_date")
            is_preorder = bool(record.get("isPreorder"))
            if not is_preorder and isinstance(preorder_date, str) and preorder_date.strip():
                try:
                    is_preorder = (
                        datetime.fromisoformat(preorder_date.strip()).replace(
                            tzinfo=timezone.utc
                        )
                        > datetime.now(timezone.utc)
                    )
                except ValueError:
                    pass
            image = record.get("image_url_1")
            product_url = f"{store['url']}/fr/{record['id']}"
            normalized_products[product_url] = {
                "title": unescape(title),
                "handle": None,
                "url": product_url,
                "product_type": "Pokémon TCG",
                "tags": [
                    *([PARKAGE_LANGUAGES[record["lang"]]] if record.get("lang") in PARKAGE_LANGUAGES else []),
                    *(["Précommande"] if is_preorder else []),
                ],
                "images": (
                    [{"src": PARKAGE_IMAGE_BASE + image.lstrip("/"), "alt": title}]
                    if isinstance(image, str) and image.strip()
                    else []
                ),
                "variants": [
                    {
                        "available": (isinstance(stock, int) and stock > 0) or is_preorder,
                        "price": str(price),
                        "inventory_quantity": stock,
                    }
                ],
            }
    products, recognized_count = parse_shopify_products(
        {"products": list(normalized_products.values())}, store
    )
    store["source_status"] = (
        f"API publique Parkage ({recognized_count} produits scellés)"
    )
    return products, recognized_count


class ProductJsonLdParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.capture_script = False
        self.script_parts = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "script":
            return
        attributes = dict(attrs)
        self.capture_script = (
            attributes.get("type", "").split(";", 1)[0].strip().lower()
            == "application/ld+json"
        )
        self.script_parts = []

    def handle_data(self, data):
        if self.capture_script:
            self.script_parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self.capture_script:
            self.documents.append("".join(self.script_parts))
            self.capture_script = False
            self.script_parts = []


def _jsonld_products(value):
    if isinstance(value, list):
        for item in value:
            yield from _jsonld_products(item)
        return
    if not isinstance(value, dict):
        return
    product_type = value.get("@type", [])
    if isinstance(product_type, str):
        product_type = [product_type]
    if isinstance(product_type, list) and any(
        isinstance(item, str) and item.rsplit("/", 1)[-1] == "Product"
        for item in product_type
    ):
        yield value
    for key in ("@graph", "mainEntity", "itemListElement", "item"):
        if key in value:
            yield from _jsonld_products(value[key])


def parse_product_jsonld(document, store):
    parser = ProductJsonLdParser()
    parser.feed(document)
    normalized_products = []
    for json_document in parser.documents:
        try:
            data = json.loads(json_document)
        except json.JSONDecodeError:
            LOG.debug("%s: ignoring malformed JSON-LD product metadata", store["name"])
            continue
        for product in _jsonld_products(data):
            title = product.get("name")
            product_url = product.get("url")
            if isinstance(product_url, dict):
                product_url = product_url.get("@id")
            if not isinstance(title, str) or not isinstance(product_url, str):
                continue
            categories = product.get("category", "")
            if isinstance(categories, list):
                categories = " ".join(
                    item for item in categories if isinstance(item, str)
                )
            elif not isinstance(categories, str):
                categories = ""
            images = product.get("image", [])
            if isinstance(images, str):
                images = [images]
            elif isinstance(images, dict):
                images = [images.get("url") or images.get("@id")]
            if not isinstance(images, list):
                images = []
            raw_offers = product.get("offers", [])
            if isinstance(raw_offers, dict):
                raw_offers = raw_offers.get("offers", [raw_offers])
            if not isinstance(raw_offers, list):
                raw_offers = []
            variants = []
            for offer in raw_offers:
                if not isinstance(offer, dict):
                    continue
                currency = offer.get("priceCurrency")
                price = offer.get("price", offer.get("lowPrice"))
                availability = str(offer.get("availability", "")).rsplit("/", 1)[-1]
                variants.append({
                    "available": (
                        availability
                        in ("InStock", "LimitedAvailability", "PreOrder")
                    ),
                    "price": price if currency in (None, "EUR") else None,
                })
            normalized_products.append({
                "title": title,
                "handle": None,
                "url": product_url,
                "product_type": categories,
                "tags": [],
                "images": [
                    {"src": urljoin(store["url"] + "/", image), "alt": title}
                    for image in images
                    if isinstance(image, str) and image.strip()
                ],
                "variants": variants,
            })
    return parse_shopify_products(
        {"products": normalized_products},
        store,
    )


def _read_public_response(url, *, accept, store, extra_headers=None):
    request = Request(
        url,
        headers={
            **(extra_headers or {}),
            "Accept": accept,
            "Accept-Language": "fr-FR,fr;q=0.9",
            "User-Agent": BROWSER_USER_AGENT,
        },
    )
    with urlopen(request, timeout=20) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        final_url = response.geturl()
        charset = response.headers.get_content_charset() or "utf-8"
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError(
            f"{store['name']} public catalog response exceeded {MAX_RESPONSE_BYTES} bytes"
        )
    requested = urlparse(store["url"])
    final = urlparse(final_url)
    requested_host = (requested.hostname or "").lower().removeprefix("www.")
    final_host = (final.hostname or "").lower().removeprefix("www.")
    if (
        final.scheme != "https"
        or final_host != requested_host
        or final.username
        or final.password
    ):
        raise ValueError(
            f"{store['name']} catalog redirected outside its configured HTTPS host"
        )
    return body, charset


def _try_auto_json_catalog(store, endpoint, parser):
    try:
        body, _ = _read_public_response(
            endpoint,
            accept="application/json",
            store=store,
        )
    except HTTPError as error:
        if error.code in (404, 405, 410):
            return None
        raise
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    parsed_products = parser(payload, store)
    if parsed_products[1] > 0:
        return parsed_products
    return None


def _record_auto_access_refusal(store, error):
    if error.code not in (401, 403):
        raise error
    store["source_status"] = (
        f"Endpoint public indisponible (HTTP {error.code})"
    )
    LOG.warning(
        "%s: public catalog endpoint refused access (HTTP %s); "
        "automatic discovery will not try alternate routes.",
        store["name"],
        error.code,
    )
    return {}, 0


def check_auto_store(store):
    limit = min(store.get("product_limit", SHOPIFY_PRODUCT_LIMIT), 250)
    try:
        result = fetch_shopify_catalog(store)
    except HTTPError as error:
        if error.code not in (404, 405, 410):
            return _record_auto_access_refusal(store, error)
        result = None
    except ValueError:
        result = None
    if result is not None and result[1] > 0:
        return result

    woo_endpoint = (
        f"{store['url']}/wp-json/wc/store/v1/products?"
        f"{urlencode({'search': 'pokemon', 'per_page': min(limit, 100)})}"
    )
    try:
        result = _try_auto_json_catalog(
            store,
            woo_endpoint,
            lambda payload, current_store: (
                parse_woocommerce_products(payload, current_store)
                if isinstance(payload, list)
                else ({}, 0)
            ),
        )
    except HTTPError as error:
        return _record_auto_access_refusal(store, error)
    if result is not None:
        store["source_status"] = "API Store WooCommerce publique"
        return result

    try:
        body, charset = _read_public_response(
            store["url"] + "/",
            accept="text/html,application/xhtml+xml",
            store=store,
        )
    except HTTPError as error:
        return _record_auto_access_refusal(store, error)
    result = parse_product_jsonld(body.decode(charset, errors="replace"), store)
    if result[1] > 0:
        store["source_status"] = "Produits JSON-LD publics"
        return result
    store["source_status"] = "Aucune source publique standard détectée"
    return {}, 0


def is_sealed_pokemon_product(product):
    title = product.get("title")
    if not isinstance(title, str) or not title.strip():
        return False
    tags = product.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    if not isinstance(tags, list):
        tags = []
    product_type = product.get("product_type", "")
    if not isinstance(product_type, str):
        product_type = ""
    normalized_type = normalize(product_type)
    if normalized_type in ("carte pokemon", "pokemon card", "pokemon single"):
        return False
    vendor = product.get("vendor", "")
    text = normalize(
        " ".join(
            [title, product_type, str(vendor) if vendor else "", *[str(tag) for tag in tags]]
        )
    )
    return (
        "pokemon" in text
        and any(contains_product_term(text, term) for term in SEALED_PRODUCT_TERMS)
        and not any(
            contains_product_term(text, term) for term in EXCLUDED_PRODUCT_TERMS
        )
    )


def parse_public_quantity(value):
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str):
        normalized_value = value.strip()
        if re.fullmatch(r"\d+", normalized_value):
            return int(normalized_value)
    return None


def extract_public_quantity(product, available_variants):
    quantity_keys = ("inventory_quantity", "available_quantity", "quantity")
    variant_quantities = []
    for variant in available_variants:
        quantity = next(
            (
                parsed
                for key in quantity_keys
                if (parsed := parse_public_quantity(variant.get(key))) is not None
            ),
            None,
        )
        if quantity is None:
            variant_quantities = []
            break
        variant_quantities.append(quantity)
    if variant_quantities:
        return sum(variant_quantities), "variantes du flux Shopify"

    for key in quantity_keys:
        quantity = parse_public_quantity(product.get(key))
        if quantity is not None:
            return quantity, "champ produit du flux Shopify"
    return None, None


class ProductDescriptionText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.ignored_depth += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data):
        if not self.ignored_depth:
            self.parts.append(data)


EXPLICIT_RETAIL_PRICE_PATTERN = re.compile(
    r"\b(?:prix\s+(?:(?:public|de\s+detail)\s+)?(?:conseille|recommande)"
    r"|prix\s+retail(?:\s+officiel)?|pvc|msrp|rrp)\b"
    r"[^0-9€]{0,40}"
    r"(?:€\s*)?"
    r"(?P<amount>(?:\d{1,3}(?:[ .]\d{3})+|\d+)(?:[,.]\d{1,2})?)"
    r"\s*(?:€|eur)(?![a-z])",
    re.IGNORECASE,
)


def parse_euro_price(value):
    normalized = value.strip().replace(" ", "")
    if "," in normalized and "." in normalized:
        decimal_separator = "," if normalized.rfind(",") > normalized.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        normalized = normalized.replace(thousands_separator, "")
        normalized = normalized.replace(decimal_separator, ".")
    elif "," in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    elif normalized.count(".") > 1:
        last_separator = normalized.rfind(".")
        normalized = normalized[:last_separator].replace(".", "") + normalized[last_separator:]
    try:
        price = Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None
    return price if price.is_finite() and price > 0 else None


AFFILIATE_FEED_FIELDS = {
    "name": (
        "name",
        "title",
        "product_name",
        "product_title",
        "productname",
    ),
    "price": (
        "price",
        "search_price",
        "sale_price",
        "current_price",
        "product_price",
    ),
    "stock": (
        "stock",
        "availability",
        "in_stock",
        "is_in_stock",
        "stock_status",
        "quantity",
    ),
    "affiliate_url": (
        "affiliate_url",
        "aw_deep_link",
        "deep_link",
        "merchant_deep_link",
        "product_url",
        "url",
        "link",
    ),
}


def _feed_field_name(value):
    return re.sub(r"[^a-z0-9]+", "_", normalize(value)).strip("_")


def _feed_text(value):
    if value is None:
        return ""
    return unescape(str(value)).strip()


def _parse_feed_stock(value):
    normalized = normalize(_feed_text(value))
    if not normalized:
        return None
    available_values = {
        "1",
        "true",
        "yes",
        "available",
        "in stock",
        "instock",
        "en stock",
        "disponible",
        "in_stock",
    }
    unavailable_values = {
        "0",
        "false",
        "no",
        "unavailable",
        "out of stock",
        "outofstock",
        "rupture",
        "rupture de stock",
        "epuise",
        "indisponible",
        "out_of_stock",
    }
    if normalized in available_values:
        return True
    if normalized in unavailable_values:
        return False
    if re.fullmatch(r"\d+", normalized):
        return int(normalized) > 0
    return None


def _parse_feed_price(value):
    text = _feed_text(value)
    if not text:
        return None
    match = re.search(
        r"(?<!\w)(?:\d{1,3}(?:[ .]\d{3})+|\d{1,3}(?:,\d{3})+|\d+)"
        r"(?:[,.]\d{1,2})?(?!\w)",
        text,
    )
    return parse_euro_price(match.group(0)) if match else None


def _normalize_affiliate_product(record):
    normalized_record = {
        _feed_field_name(key): value for key, value in record.items()
    }
    result = {}
    for output_field, aliases in AFFILIATE_FEED_FIELDS.items():
        value = next(
            (
                normalized_record[alias]
                for alias in aliases
                if alias in normalized_record
                and _feed_text(normalized_record[alias])
            ),
            None,
        )
        if output_field == "name":
            value = _feed_text(value) if value is not None else ""
            if not value:
                return None
        elif output_field == "price":
            value = _parse_feed_price(value)
        elif output_field == "stock":
            value = _parse_feed_stock(value)
        else:
            value = _feed_text(value) if value is not None else ""
        result[output_field] = value
    if result["price"] is None:
        raise ValueError(
            f"Affiliate feed product {result['name']!r} has no valid price"
        )
    if not result["affiliate_url"]:
        raise ValueError(
            f"Affiliate feed product {result['name']!r} has no affiliate URL"
        )
    parsed_url = urlparse(result["affiliate_url"])
    if parsed_url.scheme not in ("http", "https") or not parsed_url.hostname:
        raise ValueError(
            f"Affiliate feed product {result['name']!r} has an invalid affiliate URL"
        )
    return result


def _xml_local_name(tag):
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def _xml_product_records(root):
    product_tags = {"product", "item", "offer", "entry"}
    product_fields = {
        alias
        for aliases in AFFILIATE_FEED_FIELDS.values()
        for alias in aliases
    }
    records = []
    for element in root.iter():
        if _feed_field_name(_xml_local_name(element.tag)) not in product_tags:
            continue
        record = {}
        for child in element.iter():
            if child is element:
                continue
            field_name = _feed_field_name(_xml_local_name(child.tag))
            if field_name in product_fields and field_name not in record:
                record[field_name] = "".join(child.itertext()).strip()
        for key, value in element.attrib.items():
            field_name = _feed_field_name(_xml_local_name(key))
            if field_name in product_fields and field_name not in record:
                record[field_name] = value
        if record:
            records.append(record)
    return records


def parse_affiliate_product_feed(source, *, feed_format=None):
    """Parse an Awin-style XML/CSV product feed into normalized product records.

    Each returned record contains ``name``, ``price`` (Decimal), ``stock``
    (bool or None), and ``affiliate_url``. ``source`` may be a Path, bytes,
    text, or a readable binary/text stream. The function does not perform
    network requests.
    """
    if isinstance(source, Path):
        raw_data = source.read_bytes()
    elif hasattr(source, "read"):
        raw_data = source.read()
    elif isinstance(source, bytes):
        raw_data = source
    elif isinstance(source, str):
        raw_data = source
    else:
        raise TypeError("Feed source must be a Path, text, bytes, or readable stream")

    if isinstance(raw_data, bytes):
        if len(raw_data) > MAX_RESPONSE_BYTES:
            raise ValueError(
                f"Affiliate feed exceeded the {MAX_RESPONSE_BYTES}-byte limit"
            )
        text = raw_data.decode("utf-8-sig")
    elif isinstance(raw_data, str):
        if len(raw_data.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise ValueError(
                f"Affiliate feed exceeded the {MAX_RESPONSE_BYTES}-byte limit"
            )
        text = raw_data.lstrip("\ufeff")
    else:
        raise TypeError("Readable feed stream must return text or bytes")

    selected_format = (feed_format or "").strip().casefold()
    if not selected_format:
        selected_format = "xml" if text.lstrip().startswith("<") else "csv"
    if selected_format not in ("xml", "csv"):
        raise ValueError("Feed format must be 'xml' or 'csv'")

    if selected_format == "xml":
        if re.search(r"<!DOCTYPE|<!ENTITY", text, re.IGNORECASE):
            raise ValueError("Affiliate XML feeds must not contain DTD or entities")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as error:
            raise ValueError("Affiliate XML feed is malformed") from error
        records = _xml_product_records(root)
    else:
        try:
            sample = text[:8192]
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text, newline=""), dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("Affiliate CSV feed has no header row")
        records = list(reader)

    products = []
    for record in records:
        product = _normalize_affiliate_product(record)
        if product is not None:
            products.append(product)
    return products


def extract_explicit_retail_price(product):
    for key in ("recommended_retail_price", "suggested_retail_price", "msrp"):
        price = parse_euro_price(str(product.get(key, "")))
        if price is not None:
            return price, key

    description = product.get("body_html")
    if not isinstance(description, str) or not description.strip():
        return None, None
    parser = ProductDescriptionText()
    parser.feed(description)
    text = normalize(" ".join(parser.parts))
    match = EXPLICIT_RETAIL_PRICE_PATTERN.search(text)
    if not match:
        return None, None
    price = parse_euro_price(match.group("amount"))
    if price is None:
        return None, None
    return price, "prix conseillé explicitement indiqué dans la description"


_ebay_access_token = None
_ebay_token_expires_at = 0


def ebay_credentials():
    client_id = os.environ.get("EBAY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("EBAY_CLIENT_SECRET", "").strip()
    if bool(client_id) != bool(client_secret):
        raise ValueError(
            "Set both EBAY_CLIENT_ID and EBAY_CLIENT_SECRET to enable eBay lookups"
        )
    return client_id, client_secret


def ebay_api_json(url, *, headers, data=None):
    request = Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urlopen(request, timeout=15) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("eBay API response exceeded the configured size limit")
    try:
        result = json.loads(body)
    except json.JSONDecodeError as error:
        raise ValueError("eBay API returned invalid JSON") from error
    if not isinstance(result, dict):
        raise ValueError("eBay API returned an invalid response")
    return result


def ebay_application_token(client_id, client_secret):
    global _ebay_access_token, _ebay_token_expires_at
    now = time.time()
    if _ebay_access_token and now < _ebay_token_expires_at - 60:
        return _ebay_access_token
    credentials = base64.b64encode(
        f"{client_id}:{client_secret}".encode("utf-8")
    ).decode("ascii")
    token_payload = urlencode(
        {
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }
    ).encode("ascii")
    result = ebay_api_json(
        "https://api.ebay.com/identity/v1/oauth2/token",
        data=token_payload,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    token = result.get("access_token")
    expires_in = result.get("expires_in")
    if (
        not isinstance(token, str)
        or not token
        or not isinstance(expires_in, int)
        or expires_in <= 0
    ):
        raise ValueError("eBay OAuth response did not contain a valid access token")
    _ebay_access_token = token
    _ebay_token_expires_at = now + expires_in
    return token


EBAY_GENERIC_TITLE_TOKENS = {
    "pokemon",
    "tcg",
    "trading",
    "card",
    "cards",
    "sealed",
    "new",
    "booster",
    "boosters",
    "pack",
    "packs",
    "box",
    "boxes",
    "display",
    "collection",
    "coffret",
    "edition",
    "set",
    "lot",
    "of",
    "the",
    "and",
    "for",
    "fr",
    "en",
}
EBAY_NON_SEALED_LISTING_TERMS = (
    "single card",
    "individual card",
    "card only",
    "empty box",
    "proxy",
)


def ebay_title_match_score(product_title, listing_title):
    product_tokens = set(re.findall(r"[a-z0-9]+", normalize(product_title)))
    core_tokens = product_tokens - EBAY_GENERIC_TITLE_TOKENS
    listing_tokens = set(re.findall(r"[a-z0-9]+", normalize(listing_title)))
    if not core_tokens:
        return 0
    return len(core_tokens & listing_tokens) / len(core_tokens)


def search_ebay_market_average(product_title, client_id, client_secret):
    token = ebay_application_token(client_id, client_secret)
    endpoint = "https://api.ebay.com/buy/browse/v1/item_summary/search?"
    query = urlencode(
        {
            "q": product_title,
            "limit": 50,
            "filter": "buyingOptions:{FIXED_PRICE}",
        }
    )
    result = ebay_api_json(
        endpoint + query,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_FR",
        },
    )
    summaries = result.get("itemSummaries", [])
    if not isinstance(summaries, list):
        return None

    prices = []
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        listing_title = summary.get("title")
        price_data = summary.get("price")
        if not isinstance(listing_title, str) or not isinstance(price_data, dict):
            continue
        normalized_listing_title = normalize(listing_title)
        if any(
            contains_product_term(normalized_listing_title, term)
            for term in EBAY_NON_SEALED_LISTING_TERMS
        ):
            continue
        if ebay_title_match_score(product_title, listing_title) < 0.60:
            continue
        if price_data.get("currency") != "EUR":
            continue
        try:
            price = Decimal(str(price_data.get("value", "")))
        except (InvalidOperation, ValueError):
            continue
        if price.is_finite() and price > 0:
            prices.append(price)

    if len(prices) < 3:
        return None
    average = (sum(prices) / len(prices)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return {"price": str(average), "listing_count": len(prices)}


def load_market_price_cache(path):
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as cache_file:
        cache = json.load(cache_file)
    if not isinstance(cache, dict):
        raise ValueError(f"{path} contains invalid market-price cache data")
    return cache


def save_market_price_cache(path, cache):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as cache_file:
        json.dump(cache, cache_file, ensure_ascii=False, indent=2)
        cache_file.write("\n")
    temporary_path.replace(path)


def cached_ebay_market_price(product_title, cache, *, now=None):
    try:
        client_id, client_secret = ebay_credentials()
    except ValueError as error:
        LOG.warning("%s; using the configured retail estimate.", error)
        return None
    if not client_id:
        return None
    now = now or datetime.now(timezone.utc)
    cache_key = hashlib.sha256(
        normalize(product_title).encode("utf-8")
    ).hexdigest()
    entry = cache.get(cache_key)
    if isinstance(entry, dict):
        try:
            checked_at = datetime.fromisoformat(
                entry.get("checked_at", "").replace("Z", "+00:00")
            )
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=timezone.utc)
            ttl = (
                EBAY_PRICE_CACHE_TTL_SECONDS
                if entry.get("price") is not None
                else EBAY_EMPTY_CACHE_TTL_SECONDS
            )
            if (now - checked_at).total_seconds() < ttl:
                return entry if entry.get("price") is not None else None
        except (AttributeError, TypeError, ValueError):
            pass

    try:
        market_result = search_ebay_market_average(
            product_title, client_id, client_secret
        )
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        LOG.warning(
            "eBay market-price lookup failed for %r (%s)",
            product_title,
            type(error).__name__,
        )
        cache[cache_key] = {
            "price": None,
            "listing_count": 0,
            "checked_at": now.isoformat(timespec="seconds"),
        }
        return None
    entry = {
        "price": market_result["price"] if market_result else None,
        "listing_count": market_result["listing_count"] if market_result else 0,
        "checked_at": now.isoformat(timespec="seconds"),
    }
    cache[cache_key] = entry
    return entry if entry["price"] is not None else None


def apply_ebay_market_reference(product, cache):
    if product.get("retail_price") is not None or not product.get("name"):
        return False
    market_result = cached_ebay_market_price(product["name"], cache)
    if market_result is None:
        return False
    market_price = Decimal(market_result["price"])
    product["retail_price"] = str(market_price)
    product["retail_reference"] = {
        "type": "market_average",
        "label": "Moyenne des annonces actives eBay France",
        "source": "eBay Browse API",
        "listing_count": market_result["listing_count"],
        "checked_at": market_result["checked_at"],
    }
    price = product.get("price")
    product["price_delta_eur"] = None
    product["price_delta_percent"] = None
    if price is not None:
        price_delta = Decimal(price) - market_price
        product["price_delta_eur"] = str(price_delta)
        product["price_delta_percent"] = str(
            price_delta * Decimal("100") / market_price
        )
    return True


def parse_shopify_products(payload, store):
    if not isinstance(payload, dict) or not isinstance(payload.get("products"), list):
        raise ValueError(f"{store['name']} response has no Shopify products list")

    products = {}
    recognized_count = 0
    store_host = urlparse(store["url"]).hostname
    for product in payload["products"]:
        if not isinstance(product, dict) or not is_sealed_pokemon_product(product):
            continue
        recognized_count += 1
        variants = product.get("variants", [])
        available_variants = (
            [
                variant
                for variant in variants
                if isinstance(variant, dict) and variant.get("available") is True
            ]
            if isinstance(variants, list)
            else []
        )
        if not available_variants:
            continue

        handle = product.get("handle")
        raw_url = product.get("url")
        if isinstance(raw_url, str) and raw_url.strip():
            product_url = urljoin(store["url"] + "/", raw_url)
        elif isinstance(handle, str) and handle.strip():
            product_url = urljoin(store["url"] + "/", f"products/{handle}")
        else:
            LOG.warning("%s: skipping product without a valid handle", store["name"])
            continue
        parsed_url = urlparse(product_url)
        if parsed_url.scheme != "https" or parsed_url.hostname != store_host:
            LOG.warning("%s: skipping product with off-store URL", store["name"])
            continue

        images = product.get("images", [])
        image = None
        if isinstance(images, list) and images and isinstance(images[0], dict):
            image = images[0].get("src")
        if not image and isinstance(product.get("image"), dict):
            image = product["image"].get("src")
        image_metadata = images if isinstance(images, list) else []
        if isinstance(product.get("image"), dict):
            image_metadata.append(product["image"])
        image_keywords = [
            str(value)
            for entry in image_metadata
            if isinstance(entry, dict)
            for value in (entry.get("alt"), entry.get("src"))
            if isinstance(value, str) and value.strip()
        ]
        tags = product.get("tags", [])
        if isinstance(tags, list):
            tags_text = " ".join(str(tag) for tag in tags)
        else:
            tags_text = str(tags)
        if isinstance(image, str):
            image_url = urlparse(image)
            if image_url.scheme != "https" or not image_url.netloc:
                image = None
        series_keywords = " ".join(
            [
                product["title"],
                str(product.get("product_type", "")),
                str(product.get("vendor", "")),
                str(product.get("handle", "")),
                tags_text,
                *image_keywords,
            ]
        )

        prices = []
        for variant in available_variants:
            try:
                price = Decimal(str(variant.get("price", "")))
            except (InvalidOperation, ValueError):
                continue
            if price.is_finite() and price >= 0:
                prices.append(price)
        price = min(prices) if prices else None
        quantity, quantity_source = extract_public_quantity(
            product, available_variants
        )
        retail_price = (
            store.get("retail_prices", {}).get(handle)
            if isinstance(handle, str)
            else None
        )
        retail_reference = None
        retail_fallback = None
        if retail_price is not None:
            retail_reference = {"type": "official"}
        else:
            detected_retail, retail_source = extract_explicit_retail_price(product)
            if detected_retail is not None:
                retail_price = detected_retail
                retail_reference = {
                    "type": "official_metadata",
                    "source": retail_source,
                }
            else:
                category = classify_retail_estimate(
                    product["title"], series_keywords
                )
                estimate = store.get("retail_estimates", {}).get(category)
                if estimate is not None:
                    retail_fallback = {
                        "type": "estimate",
                        "label": estimate["label"],
                        "minimum": str(estimate["minimum"]),
                        "maximum": str(estimate["maximum"]),
                    }
        price_delta_percent = None
        price_delta_eur = None
        if price is not None and retail_price is not None:
            price_delta_eur = price - retail_price
            price_delta_percent = (
                price_delta_eur * Decimal("100") / retail_price
            )

        canonical_url = urlunparse(parsed_url._replace(fragment=""))
        flag_text = " ".join(
            [
                product["title"],
                str(product.get("product_type", "")),
                tags_text,
                str(product.get("handle", "")),
            ]
        )
        products[canonical_url] = {
            "is_preorder": detect_preorder(flag_text),
            "is_hot_series": detect_hot_series(flag_text),
            "name": product["title"].strip(),
            "series_keywords": series_keywords,
            "url": canonical_url,
            "image": image if isinstance(image, str) else None,
            "price": str(price) if price is not None else None,
            "price_is_from": len(set(prices)) > 1,
            "retail_price": (
                str(retail_price) if retail_price is not None else None
            ),
            "retail_reference": retail_reference,
            "retail_fallback": retail_fallback,
            "price_delta_percent": (
                str(price_delta_percent)
                if price_delta_percent is not None
                else None
            ),
            "price_delta_eur": (
                str(price_delta_eur) if price_delta_eur is not None else None
            ),
            "quantity": quantity,
            "quantity_source": quantity_source,
        }
    return products, recognized_count


def classify_retail_estimate(title, keywords=""):
    normalized_product_title = normalize(title)
    if contains_product_term(normalized_product_title, "display"):
        return "display_36"
    normalized_title = normalize(f"{title} {keywords}")
    has_30_boosters = any(
        contains_product_term(normalized_title, term)
        for term in ("30 boosters", "30 booster", "30 packs", "30 pack")
    )
    is_japanese = any(
        contains_product_term(normalized_title, term)
        for term in ("japonais", "japanese", "japan", "jpn")
    )
    if is_japanese and has_30_boosters:
        return "japanese_box_30"

    has_15_boosters = any(
        contains_product_term(normalized_title, term)
        for term in ("15 boosters", "15 packs", "15 pack")
    )
    is_chinese = any(
        contains_product_term(normalized_title, term)
        for term in ("chinois", "chinese", "china", "cn")
    )
    if is_chinese and has_15_boosters and any(
        contains_product_term(normalized_title, term)
        for term in ("display", "boite", "box")
    ):
        return "chinese_display_15"

    if any(
        contains_product_term(normalized_title, term)
        for term in ("etb", "elite trainer", "dresseur d'elite", "dresseur elite")
    ):
        return "etb"
    if contains_product_term(normalized_title, "mini tin"):
        return "mini_tin"
    if any(
        contains_product_term(normalized_title, term)
        for term in ("tin cube", "pokebox", "poke box", "pokemon tin")
    ):
        return "tin_cube"
    if any(
        contains_product_term(normalized_title, term)
        for term in (
            "duopack",
            "duo pack",
            "double pack",
            "tripack",
            "tri-pack",
            "triple pack",
        )
    ) or (
        contains_product_term(normalized_title, "blister")
        and any(
            contains_product_term(normalized_title, term)
            for term in ("2 boosters", "3 boosters")
        )
    ):
        return "duopack_tripack"
    if (
        contains_product_term(normalized_title, "bundle")
        and contains_product_term(normalized_title, "6 boosters")
    ) or contains_product_term(normalized_title, "lot de 6 boosters"):
        return "bundle_6"
    if contains_product_term(normalized_title, "coffret"):
        return "coffret"
    return None


def apply_retail_estimate_fallback(product):
    estimate = product.pop("retail_fallback", None)
    if product.get("retail_price") is not None or not isinstance(estimate, dict):
        return False
    minimum = Decimal(estimate["minimum"])
    maximum = Decimal(estimate["maximum"])
    retail_price = ((minimum + maximum) / Decimal("2")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    product["retail_price"] = str(retail_price)
    product["retail_reference"] = estimate
    price = product.get("price")
    if price is not None:
        price_delta = Decimal(price) - retail_price
        product["price_delta_eur"] = str(price_delta)
        product["price_delta_percent"] = str(
            price_delta * Decimal("100") / retail_price
        )
    return True


def is_good_deal(product):
    try:
        price = Decimal(str(product.get("price")))
        reference = product.get("retail_reference") or {}
        if reference.get("type") == "estimate":
            return price < Decimal(str(reference["minimum"]))
        return Decimal(str(product.get("price_delta_percent"))) < 0
    except (InvalidOperation, ValueError, KeyError, TypeError):
        return False


def fetch_affiliate_feed_products(store):
    request = Request(
        store["feed_url"],
        headers={
            "Accept": "application/xml,text/xml,text/csv,text/plain;q=0.9,*/*;q=0.8",
            "User-Agent": "PokemonRestockMonitor/1.0 (+public affiliate feed reader)",
        },
    )
    try:
        with urlopen(request, timeout=25) as response:
            feed_data = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError:
        raise
    except URLError:
        LOG.warning("%s: network error while reading affiliate product feed", store["name"])
        raise
    if len(feed_data) > MAX_RESPONSE_BYTES:
        raise ValueError(
            f"{store['name']} affiliate feed exceeded {MAX_RESPONSE_BYTES} bytes"
        )

    records = parse_affiliate_product_feed(
        feed_data,
        feed_format=store.get("feed_format"),
    )
    products = {}
    recognized_count = 0
    for record in records:
        if not is_sealed_pokemon_product(
            {
                "title": record["name"],
                "product_type": "Pokémon TCG",
                "tags": [],
            }
        ):
            continue
        recognized_count += 1
        if record["stock"] is not True:
            continue
        product = {
            "name": record["name"],
            "is_preorder": detect_preorder(record["name"]),
            "is_hot_series": detect_hot_series(record["name"]),
            "series_keywords": "",
            "url": record["affiliate_url"],
            "image": None,
            "price": str(record["price"]),
            "price_is_from": False,
            "retail_price": None,
            "price_delta_eur": None,
            "price_delta_percent": None,
            "quantity": None,
            "retail_prices": store.get("retail_prices", {}),
            "retail_estimates": store.get("retail_estimates", {}),
        }
        products[record["affiliate_url"]] = product
    return products, recognized_count


def check_store(store):
    if store.get("platform") == "parkage_api" and store.get("feed_url") is None:
        return fetch_parkage_catalog(store)
    if store.get("platform") == "prestashop_json" and store.get("feed_url") is None:
        return fetch_prestashop_catalog(store)
    if (
        store.get("platform") == "shopify"
        and store.get("source_mode") == "auto"
        and store.get("feed_url") is None
    ):
        return fetch_shopify_catalog(store)
    if (
        store.get("platform") == "ultrajeux_html"
        and store.get("feed_url") is None
    ):
        return fetch_ultrajeux_catalog(store)
    if store.get("source_mode") == "auto" or store.get("platform") == "auto":
        if store.get("feed_url") is not None:
            store["source_status"] = "Flux produit configuré"
            return fetch_affiliate_feed_products(store)
        return check_auto_store(store)
    if store.get("feed_url") is not None:
        return fetch_affiliate_feed_products(store)
    if store.get("platform") == "woocommerce":
        return fetch_woocommerce_products(store)
    if store.get("platform") == "philibert_html":
        return fetch_philibert_catalog(store)
    return fetch_shopify_catalog(store)


def post_json(url, payload, *, telegram=False):
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json", 
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        },
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        result = response.read(64_000)
    if telegram and not json.loads(result).get("ok"):
        raise RuntimeError("Telegram rejected the notification")


def test_discord_webhook():
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    parsed = urlparse(webhook)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "discord.com"
        or not parsed.path.startswith("/api/webhooks/")
    ):
        raise ValueError(
            "DISCORD_WEBHOOK_URL is missing or is not a Discord webhook URL"
        )
    request = Request(
        webhook,
        data=json.dumps(
            {"content": "✅ Test du moniteur Pokémon TCG : webhook Discord opérationnel."}
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json", 
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            LOG.info("Discord test message accepted (HTTP %s)", response.status)
    except HTTPError as error:
        raise RuntimeError(
            f"Discord rejected the test message (HTTP {error.code})"
        ) from None
    except URLError:
        raise RuntimeError("Could not connect to Discord for the test message") from None


def send_alert(product, store_name, *, test=False):
    price = product.get("price")
    if price is not None:
        formatted_price = f"{Decimal(price):,.2f}".replace(",", " ").replace(".", ",")
        price_label = f"À partir de {formatted_price} €" if product.get("price_is_from") else f"{formatted_price} €"
    else:
        price_label = (
            "Prix à venir"
            if product.get("is_preorder")
            else "Non communiqué par la boutique"
        )
    quantity = product.get("quantity")
    quantity_label = (
        f"{quantity} (donnée du flux public)"
        if isinstance(quantity, int) and not isinstance(quantity, bool)
        else "Non communiquée par la boutique"
    )
    retail_price = product.get("retail_price")
    retail_reference = product.get("retail_reference")
    if retail_reference is None and retail_price is not None:
        retail_reference = {"type": "official"}
    retail_is_estimate = (
        isinstance(retail_reference, dict)
        and retail_reference.get("type") == "estimate"
    )
    if retail_price is not None:
        retail_formatted = (
            f"{Decimal(retail_price):,.2f}".replace(",", " ").replace(".", ",")
        )
        if retail_is_estimate:
            minimum = f"{Decimal(retail_reference['minimum']):,.2f}".replace(
                ",", " "
            ).replace(".", ",")
            maximum = f"{Decimal(retail_reference['maximum']):,.2f}".replace(
                ",", " "
            ).replace(".", ",")
            estimated_range = (
                f"{minimum} €"
                if retail_reference["minimum"] == retail_reference["maximum"]
                else f"{minimum}–{maximum} €"
            )
            retail_label = f"{estimated_range} (base de calcul : {retail_formatted} €)"
        else:
            retail_label = f"{retail_formatted} €"
        delta = product.get("price_delta_percent")
        if delta is not None:
            delta_percent = Decimal(delta).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if retail_is_estimate:
                delta_reference = "estimation"
            elif isinstance(retail_reference, dict) and retail_reference.get(
                "type"
            ) == "market_average":
                delta_reference = "marché eBay"
            else:
                delta_reference = "retail"
            delta_label = f"{delta_percent:+.2f}% vs {delta_reference}"
        else:
            delta_label = "Non calculable"
    else:
        retail_label = "Non vérifié"
        delta_label = "Non calculé"
    if retail_is_estimate:
        retail_field_label = "Estimation retail"
    elif isinstance(retail_reference, dict) and retail_reference.get(
        "type"
    ) == "market_average":
        retail_field_label = "Moyenne marché eBay"
    else:
        retail_field_label = "Prix retail officiel"
    if test:
        message = (
            f"Aperçu de test — {store_name}. "
            "Les valeurs retail et l'écart sont fictifs."
        )
    else:
        label = "Nouvelle précommande Pokémon TCG" if product.get("is_preorder") else "Nouveau produit Pokémon TCG scellé disponible"
        hot = " 🔥 Série recherchée" if product.get("is_hot_series") else ""
        if product.get("is_good_deal"):
            hot += " 💸 Bon plan"
        message = (
            f"{'📅' if product.get('is_preorder') else ''}{label}{hot} ({store_name}) : "
            f"{product['name']} — {product['url']}\n"
            f"💶 **Prix :** {price_label}\n"
            f"🏷️ **{retail_field_label} :** {retail_label}\n"
            f"📊 **Écart :** {delta_label}\n"
            f"📦 **Quantité :** {quantity_label}"
        )
    print(f"\n🔔 {message}", flush=True)
    successful = True

    discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if discord_webhook:
        try:
            if test:
                delta_eur_label = "Non calculé"
                if product.get("price_delta_eur") is not None:
                    delta_eur = f"{Decimal(product['price_delta_eur']):+,.2f}"
                    delta_eur_label = (
                        delta_eur.replace(",", " ").replace(".", ",") + " €"
                    )
                discord_description = (
                    f"Aperçu de test pour **{store_name}** — valeurs indicatives.\n"
                    f"💶 **Prix :** {price_label}\n"
                    f"🏷️ **{retail_field_label} :** {retail_label}\n"
                    f"📊 **Écart :** {delta_label} · {delta_eur_label}\n"
                    f"[Voir la fiche produit ↗]({product['url']})"
                )
                discord_title = "🧪 **Aperçu du format Pokémon TCG**"
            else:
                discord_description = (
                    f"{'📅 **Précommande** chez' if product.get('is_preorder') else '✅ En stock chez'} **{store_name}**\n"
                    f"{'🔥 **Série très recherchée**' + chr(10) if product.get('is_hot_series') else ''}"
                    f"{'💸 **Bon plan** : sous le prix de référence' + chr(10) if product.get('is_good_deal') else ''}"
                    f"💶 **Prix :** {price_label}\n"
                    f"[Voir la fiche et acheter ↗]({product['url']})"
                )
                discord_title = (
                    "📅 **Nouvelle précommande Pokémon TCG**"
                    if product.get("is_preorder")
                    else "🟢 **Nouveau stock Pokémon TCG**"
                )
            embed = {
                "title": product["name"][:256],
                "url": product["url"],
                "description": discord_description,
                "color": 0x5865F2 if test else (0xF39C12 if product.get("is_preorder") else 0x2ECC71),
            }
            if product.get("image"):
                embed["image"] = {"url": product["image"]}
            payload = {
                "content": discord_title,
                "embeds": [embed],
            }
            post_json(discord_webhook, payload)
        except HTTPError as error:
            LOG.error("Discord notification failed (HTTP %s)", error.code)
            successful = False
        except (URLError, RuntimeError, ValueError):
            LOG.error("Discord notification failed due to a network or response error")
            successful = False

    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if telegram_token and telegram_chat_id:
        try:
            if product.get("image"):
                telegram_url = f"https://api.telegram.org/bot{telegram_token}/sendPhoto"
                payload = {
                    "chat_id": telegram_chat_id,
                    "photo": product["image"],
                    "caption": message[:1024],
                }
            else:
                telegram_url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
                payload = {"chat_id": telegram_chat_id, "text": message}
            post_json(telegram_url, payload, telegram=True)
        except (HTTPError, URLError, RuntimeError, ValueError) as error:
            LOG.error("Telegram notification failed: %s", error)
            successful = False

    return successful


def retry_after_seconds(error, minimum):
    value = error.headers.get("Retry-After")
    if value:
        try:
            return max(minimum, int(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(
                    minimum,
                    int((retry_at - datetime.now(timezone.utc)).total_seconds()),
                )
            except (TypeError, ValueError, OverflowError):
                LOG.warning("Could not parse Retry-After value %r", value)
    return minimum


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    load_local_environment(Path(__file__).with_name(".env"))
    if args.test_discord:
        test_discord_webhook()
        return
    if bool(os.environ.get("TELEGRAM_BOT_TOKEN")) != bool(
        os.environ.get("TELEGRAM_CHAT_ID")
    ):
        raise ValueError(
            "Set both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable Telegram"
        )

    stores_by_host = {
        urlparse(store["url"]).hostname: store
        for store in load_stores(args.config)
    }
    for feed_store in load_affiliate_feed_stores(args.shops):
        hostname = urlparse(feed_store["url"]).hostname
        existing_store = stores_by_host.get(hostname)
        if existing_store is None:
            stores_by_host[hostname] = feed_store
        elif feed_store.get("feed_url") is not None:
            existing_store["feed_url"] = feed_store["feed_url"]
            existing_store["feed_format"] = feed_store["feed_format"]
        elif existing_store.get("platform") in ("shopify", "affiliate_feed"):
            existing_store["platform"] = (
                feed_store["platform"]
                if feed_store["platform"] != "auto"
                else "auto"
            )
            existing_store["source_mode"] = "auto"
            existing_store["id"] = feed_store["id"]
            existing_store["name"] = feed_store["name"]
            for key in ("collection", "collections", "max_pages"):
                if feed_store.get(key):
                    existing_store[key] = feed_store[key]
    stores = list(stores_by_host.values())
    state = load_state(args.state)
    stock = load_stock_snapshot(args.stock)
    market_price_cache = load_market_price_cache(args.market_cache)
    if not os.environ.get("EBAY_CLIENT_ID") and not os.environ.get(
        "EBAY_CLIENT_SECRET"
    ):
        LOG.info(
            "eBay market-price search disabled; configure EBAY_CLIENT_ID and "
            "EBAY_CLIENT_SECRET to enable cached French active-listing estimates."
        )
    LOG.info(
        "Monitoring %s public product catalogs; checking every %s seconds.",
        len(stores),
        args.interval,
    )
    LOG.info(
        "Checks configured public feeds, standard Shopify/WooCommerce catalogs, "
        "and public product JSON-LD; no access-control bypass is used."
    )

    while True:
        cycle_started = time.monotonic()
        stop_cycle = False
        retry_wait = args.interval
        for index, store in enumerate(stores):
            if index:
                time.sleep(random.uniform(8, 15))
            try:
                products, recognized_count = check_store(store)
            except HTTPError as error:
                if error.code in (429, 503):
                    stop_cycle = True
                    retry_wait = retry_after_seconds(error, args.interval)
                    break
                if store.get("source_mode") == "auto":
                    save_auto_discovery_status(
                        stock,
                        store,
                        f"Endpoint public indisponible (HTTP {error.code})",
                    )
                continue
            except (URLError, TimeoutError, ValueError) as error:
                LOG.warning(
                    "%s: could not read public product catalog: %s",
                    store["name"],
                    error,
                )
                if store.get("source_mode") == "auto":
                    status = (
                        "Erreur réseau pendant la découverte automatique"
                        if isinstance(error, (URLError, TimeoutError))
                        else "Réponse non exploitable par la découverte automatique"
                    )
                    save_auto_discovery_status(stock, store, status)
                continue

            if not recognized_count:
                LOG.warning(
                    "%s: no sealed Pokémon TCG products recognized; source=%s.",
                    store["name"],
                    store.get("source_status", "standard catalog"),
                )
                if store.get("source_mode") == "auto":
                    save_auto_discovery_status(
                        stock,
                        store,
                        store.get(
                            "source_status",
                            "Aucune source publique standard détectée",
                        ),
                    )
                continue

            market_price_updates = 0
            retail_estimate_updates = 0
            for product in products.values():
                if apply_ebay_market_reference(product, market_price_cache):
                    market_price_updates += 1
                if apply_retail_estimate_fallback(product):
                    retail_estimate_updates += 1
                product["is_good_deal"] = is_good_deal(product)
            if market_price_updates:
                LOG.info(
                    "%s: added eBay active-listing estimates for %s products",
                    store["name"],
                    market_price_updates,
                )
            if retail_estimate_updates:
                LOG.info(
                    "%s: applied format-based retail estimates to %s products "
                    "without a configured, explicit, or Web reference",
                    store["name"],
                    retail_estimate_updates,
                )

            checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            store_states = state["searches"]
            previous = store_states.get(store["id"])
            current_products = {
                url: {"name": product["name"]} for url, product in products.items()
            }
            if previous is None:
                LOG.info(
                    "%s: baseline saved (%s available products); no initial alerts",
                    store["name"],
                    len(products),
                )
                store_states[store["id"]] = {"products": current_products}
            else:
                previous_products = previous["products"]
                new_urls = current_products.keys() - previous_products.keys()
                for url, product in products.items():
                    previous_product = previous_products.get(url, {})
                    recently_available_at = previous_product.get(
                        "recently_available_at"
                    )
                    if url in new_urls:
                        recently_available_at = checked_at
                    if isinstance(recently_available_at, str):
                        product["recently_available_at"] = recently_available_at
                        current_products[url][
                            "recently_available_at"
                        ] = recently_available_at
                alerts_succeeded = True
                for url in sorted(new_urls):
                    if not send_alert(products[url], store["name"]):
                        alerts_succeeded = False
                LOG.info(
                    "%s: %s available products; %s new",
                    store["name"],
                    len(products),
                    len(new_urls),
                )
                if alerts_succeeded:
                    store_states[store["id"]] = {"products": current_products}

            retained = []
            for old_product in stock["stores"].get(store["id"], {}).get("products", []):
                old_url = old_product.get("url")
                if not isinstance(old_url, str) or old_url in products:
                    continue
                since = old_product.get("out_of_stock_since") or checked_at
                try:
                    age = datetime.fromisoformat(checked_at) - datetime.fromisoformat(since)
                except ValueError:
                    continue
                if age.days < OUT_OF_STOCK_RETENTION_DAYS:
                    retained.append(
                        {**old_product, "out_of_stock": True, "out_of_stock_since": since}
                    )
            stock["stores"][store["id"]] = {
                "name": store["name"],
                "url": store["url"],
                "checked_at": checked_at,
                "source_status": store.get("source_status"),
                "products": [*products.values(), *retained],
            }

        save_state(args.state, state)
        save_market_price_cache(args.market_cache, market_price_cache)
        stock["updated_at"] = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        save_stock_snapshot(args.stock, stock)
        elapsed = time.monotonic() - cycle_started
        wait = max(retry_wait if stop_cycle else 0, args.interval - elapsed)
        if stop_cycle:
            LOG.warning(
                "Pausing after rate limiting; next cycle in at least %.0f seconds.",
                wait,
            )
        time.sleep(wait)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        LOG.error("%s", error)
        sys.exit(1)