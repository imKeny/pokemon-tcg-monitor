#!/usr/bin/env python3
"""Serve a local dashboard for the Pokémon TCG stock monitor."""

import json
import os
import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import Flask, render_template, send_from_directory


app = Flask(__name__)
STOCK_FILE = Path(
    os.environ.get(
        "STOCK_FILE",
        str(Path(__file__).with_name("stock.json")),
    )
)
CONFIG_FILE = Path(
    os.environ.get(
        "PRODUCTS_FILE",
        str(Path(__file__).with_name("products.json")),
    )
)
SHOPS_FILE = Path(
    os.environ.get(
        "SHOPS_FILE",
        str(Path(__file__).with_name("shops.json")),
    )
)


def load_stock():
    if not STOCK_FILE.exists():
        return {"updated_at": None, "stores": {}}
    with STOCK_FILE.open(encoding="utf-8") as stock_file:
        stock = json.load(stock_file)
    if (
        not isinstance(stock, dict)
        or not isinstance(stock.get("stores"), dict)
    ):
        raise ValueError(f"{STOCK_FILE} contains invalid dashboard stock data")
    return stock


def load_shop_registry():
    registry = {}
    for path, key in (
        (CONFIG_FILE, "stores"),
        (SHOPS_FILE, "shops"),
    ):
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as config_file:
            config = json.load(config_file)
        if not isinstance(config, dict):
            raise ValueError(f"{path} must contain a JSON object")
        entries = config.get(key, config.get("shopify_stores", []))
        if not isinstance(entries, list):
            raise ValueError(f"{path} must contain a list of {key}")
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or entry.get("enabled", True) is False
                or not isinstance(entry.get("name"), str)
                or not isinstance(entry.get("url"), str)
            ):
                continue
            name = entry["name"].strip()
            shop_data = {
                "name": name,
                "url": entry["url"],
            }
            if key == "shops":
                shop_data["feed_configured"] = bool(entry.get("feed_url"))
                shop_data["source_mode"] = entry.get("source_mode", "auto")
            registry[name.casefold()] = shop_data
    return registry


def merchant_favicon_url(store):
    store_url = store.get("url")
    if not isinstance(store_url, str):
        return None
    try:
        parsed = urlsplit(store_url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
        return None
    return (
        "https://www.google.com/s2/favicons?"
        f"domain={parsed.hostname}&sz=64"
    )


def format_price(value):
    if value is None:
        return "Non communiqué"
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "Non communiqué"
    if not price.is_finite():
        return "Non communiqué"
    return f"{price:,.2f}".replace(",", " ").replace(".", ",") + " €"


def format_percentage(value):
    if value is None:
        return "—"
    try:
        percentage = Decimal(str(value)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError):
        return "—"
    if not percentage.is_finite():
        return "—"
    return f"{percentage:+.2f} %"


def format_signed_price(value):
    if value is None:
        return "—"
    try:
        amount = Decimal(str(value)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError):
        return "—"
    if not amount.is_finite():
        return "—"
    return f"{amount:+,.2f}".replace(",", " ").replace(".", ",") + " €"


app.jinja_env.filters["price"] = format_price
app.jinja_env.filters["percentage"] = format_percentage
app.jinja_env.filters["signed_price"] = format_signed_price


SERIES_PATTERNS = (
    (
        (
            "30 ans",
            "30th",
            "30eme anniversaire",
            "30e anniversaire",
            "30th anniversary",
            "30th celebration",
            "30th celebrations",
            "30e celebration",
            "30eme celebration",
        ),
        "30e anniversaire",
    ),
    (("anniversaire", "anniversary"), "Édition anniversaire"),
    (
        (
            "mid-autumn",
            "mid autumn",
            "midautumn",
            "mid-autumn festival",
            "mid autumn festival",
            "moon festival",
            "harvest moon",
        ),
        "Édition spéciale · Mid-Autumn",
    ),
    (("celebrations", "celebration", "celebrations collection"), "Célébrations"),
    (("chaos ascendant", "rising chaos"), "Chaos Ascendant"),
    (("nuit noire", "black night"), "Nuit Noire"),
    (("foudre noire", "black bolt"), "Foudre Noire"),
    (("flamme blanche", "white flare"), "Flamme Blanche"),
    (("rivalites destinees", "destined rivals"), "Rivalités Destinées"),
    (("aventures ensemble", "journey together"), "Aventures Ensemble"),
    (("etincelles deferlantes", "surging sparks"), "Étincelles Déferlantes"),
    (("151", "pokeball 151"), "Écarlate et Violet · 151"),
    (("evolutions prismatiques", "prismatic evolutions"), "Écarlate et Violet · Évolutions Prismatiques"),
    (("forces temporelles", "temporal forces"), "Écarlate et Violet · Forces Temporelles"),
    (("destinees de paldea", "paldean fates"), "Écarlate et Violet · Destinées de Paldea"),
    (("mascarade crepusculaire", "twilight masquerade"), "Écarlate et Violet · Mascarade Crépusculaire"),
    (("faille paradoxale", "paradox rift"), "Écarlate et Violet · Faille Paradoxale"),
    (("flammes obsidiennes", "obsidian flames"), "Écarlate et Violet · Flammes Obsidiennes"),
    (("ecarlate et violet", "scarlet violet"), "Écarlate et Violet"),
    (("epee et bouclier", "sword shield"), "Épée et Bouclier"),
    (("mega-amphinobi", "mega amphinobi"), "Méga-Évolution"),
    (("mega-", "mega "), "Méga-Évolution"),
    (("pokemon go",), "Pokémon GO"),
)


def identify_series(product_name, keywords=""):
    normalized_name = normalize_text(f"{product_name} {keywords}")
    for terms, label in SERIES_PATTERNS:
        if any(
            re.search(
                r"(?<![a-z0-9])"
                + re.escape(normalize_text(term))
                + r"(?![a-z0-9])",
                normalized_name,
            )
            for term in terms
        ):
            return label
    return "Série à identifier"


def normalize_text(value):
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", " ", without_accents).strip()


def format_local_datetime(value):
    if not isinstance(value, str) or not value.strip():
        return "—"
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("Europe/Paris"))
        return parsed.astimezone(ZoneInfo("Europe/Paris")).strftime("%d/%m/%Y à %H:%M")
    except (ValueError, OverflowError):
        return "—"


def merchant_mark(name):
    initials = "".join(
        word[0]
        for word in re.findall(r"[A-Za-z0-9À-ÿ]+", name)
        if word
    )
    return initials[:2].upper() or "?"


KIND_PATTERNS = (
    ("Carte gradée", ("carte gradee", "cartes gradees", "graded", "psa", "cgc", "pca", "expertisee")),
    ("Display", ("display", "boite de", "boite a boosters", "box de boosters")),
    ("ETB", ("etb", "elite trainer", "dresseur d elite")),
    ("Bundle", ("bundle",)),
    ("Tin / Pokébox", ("pokebox", "mini tin", "tin")),
    ("Blister", ("blister", "tripack", "duopack", "3 boosters", "2 boosters")),
    ("Coffret", ("coffret", "collection", "gift box", "special")),
    ("Booster", ("booster",)),
)
LANGUAGE_PATTERNS = (
    ("Japonais", ("japonais", "japonaise", "japanese", "japan", "jp", "jpn")),
    ("Coréen", ("coreen", "coreenne", "korean", "kr", "kor")),
    ("Chinois", ("chinois", "chinoise", "chinese", "cn", "tw", "zh")),
    ("Anglais", ("anglais", "anglaise", "english", "eng")),
    ("Allemand", ("allemand", "german", "deutsch")),
    ("Espagnol", ("espagnol", "spanish")),
    ("Italien", ("italien", "italian")),
)


def _matches_any(text, terms):
    return any(
        re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text)
        for term in terms
    )


PREORDER_TERMS = (
    "precommande",
    "pre commande",
    "preorder",
    "pre order",
    "a paraitre",
    "prochainement",
    "bientot disponible",
)


def identify_product_status(product):
    if product.get("out_of_stock"):
        return "rupture"
    if product.get("is_preorder") or _matches_any(
        normalize_text(
            f"{product.get('name', '')} {product.get('series_keywords', '')}"
        ),
        PREORDER_TERMS,
    ):
        return "precommande"
    return "disponible"


def identify_product_kind(product_name, keywords=""):
    text = normalize_text(f"{product_name} {keywords}")
    title = normalize_text(product_name)
    for label, terms in KIND_PATTERNS:
        if _matches_any(title, terms):
            return label
    for label, terms in KIND_PATTERNS:
        if _matches_any(text, terms):
            return label
    return "Autre"


def identify_product_language(product_name, keywords=""):
    title = normalize_text(product_name)
    text = normalize_text(f"{product_name} {keywords}")
    for source in (title, text):
        for label, terms in LANGUAGE_PATTERNS:
            if _matches_any(source, terms):
                return label
    return "Français"


NAME_STOPWORDS = frozenset(
    "pokemon pokemons en francais fr vf vo version de du la le les des et a au aux "
    "scelle scelles neuf etat defaut issu aleatoire".split()
)
VARIANT_MARKERS = frozenset(
    "premium ultra upc illustration poster pins figurine cube mini pokebox tin "
    "bundle tripack duopack blister deck collection".split()
)
SET_CODE_PATTERN = re.compile(r"\b(ev|me|eb|sv|sm|xy|sl)0*(\d{1,2})(?: (\d))?\b")


def _name_signature(product):
    text = normalize_text(product.get("name", ""))
    code_match = SET_CODE_PATTERN.search(text)
    code = None
    if code_match:
        code = code_match.group(1) + code_match.group(2) + (
            f".{code_match.group(3)}" if code_match.group(3) else ""
        )
        text = text.replace(code_match.group(0), " ")
    tokens = set()
    for token in text.split():
        if token in NAME_STOPWORDS:
            continue
        if len(token) > 3 and token.endswith("s"):
            token = token[:-1]
        tokens.add(token)
    numbers = {token for token in tokens if token.isdigit()}
    return {
        "code": code,
        "tokens": tokens - numbers,
        "numbers": numbers,
        "markers": {token for token in tokens if token in VARIANT_MARKERS},
        "kind": identify_product_kind(product.get("name", ""), product.get("series_keywords", "")),
        "lang": identify_product_language(product.get("name", ""), product.get("series_keywords", "")),
    }


def _same_product(first, second):
    if first["kind"] != second["kind"] or first["lang"] != second["lang"]:
        return False
    if first["code"] != second["code"]:
        return False
    if first["markers"] != second["markers"]:
        return False
    if first["numbers"] and second["numbers"] and first["numbers"] != second["numbers"]:
        return False
    smaller = min(len(first["tokens"]), len(second["tokens"]))
    if not smaller:
        return False
    overlap = len(first["tokens"] & second["tokens"]) / smaller
    # Sans code d'extension, on exige des noms presque identiques.
    return overlap >= (0.75 if first["code"] else 0.9)


def _offer_rank(product):
    try:
        price = Decimal(str(product.get("price")))
        if not price.is_finite():
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        price = None
    return (
        identify_product_status(product) == "rupture",
        price is None,
        price if price is not None else Decimal(0),
    )


def build_unified_view(stores):
    clusters = []
    for store_index, store in enumerate(stores):
        for product in store.get("products", []):
            signature = _name_signature(product)
            entry = (store_index, store, product)
            for cluster in clusters:
                if _same_product(cluster["signature"], signature):
                    cluster["entries"].append(entry)
                    break
            else:
                clusters.append({"signature": signature, "entries": [entry]})
    unified = []
    for cluster in clusters:
        entries = sorted(cluster["entries"], key=lambda item: _offer_rank(item[2]))
        store_index, store, best = entries[0]
        unified.append(
            {
                "product": best,
                "store": store,
                "store_index": store_index,
                "offer_count": len(entries),
                "offers": [
                    {
                        "store": other_store.get("name", "Boutique"),
                        "price": other.get("price"),
                        "url": other.get("url"),
                        "status": identify_product_status(other),
                    }
                    for _, other_store, other in entries[1:]
                ],
            }
        )
    unified.sort(key=lambda entry: entry["product"].get("name", "").casefold())
    return unified


app.jinja_env.filters["product_kind"] = identify_product_kind
app.jinja_env.filters["product_status"] = identify_product_status
app.jinja_env.filters["product_language"] = identify_product_language
app.jinja_env.filters["local_datetime"] = format_local_datetime
app.jinja_env.filters["series"] = identify_series


@app.get("/logo.jpg")
def logo():
    response = send_from_directory(
        Path(__file__).parent, "logo.jpg", mimetype="image/jpeg"
    )
    response.cache_control.max_age = 86400
    return response


@app.get("/")
def dashboard():
    stock = load_stock()
    shop_registry = load_shop_registry()
    recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    stores = sorted(
        stock["stores"].values(),
        key=lambda store: store.get("name", "").casefold(),
    )
    existing_names = set()
    for store in stores:
        normalized_name = store.get("name", "").casefold()
        existing_names.add(normalized_name)
        configured_shop = shop_registry.get(normalized_name, {})
        store["url"] = store.get("url") or configured_shop.get("url")
        if "feed_configured" in configured_shop:
            store["feed_configured"] = configured_shop["feed_configured"]
            store["source_mode"] = configured_shop["source_mode"]
        store["favicon_url"] = merchant_favicon_url(store)
        recent_count = 0
        for product in store.get("products", []):
            recent = False
            value = product.get("recently_available_at")
            if isinstance(value, str) and value.strip():
                try:
                    recently_available_at = datetime.fromisoformat(
                        value.strip().replace("Z", "+00:00")
                    )
                    if recently_available_at.tzinfo is None:
                        recently_available_at = recently_available_at.replace(
                            tzinfo=timezone.utc
                        )
                    recent = recent_cutoff <= recently_available_at <= datetime.now(
                        timezone.utc
                    )
                except (ValueError, OverflowError):
                    recent = False
            product["is_recent"] = recent
            recent_count += recent
        store["recent_count"] = recent_count
        store["merchant_mark"] = merchant_mark(store.get("name", ""))
    for normalized_name, configured_shop in shop_registry.items():
        if normalized_name in existing_names:
            continue
        store = {
            **configured_shop,
            "checked_at": None,
            "products": [],
            "recent_count": 0,
            "source_status": "Découverte automatique active",
        }
        store["favicon_url"] = merchant_favicon_url(store)
        store["merchant_mark"] = merchant_mark(store["name"])
        stores.append(store)
    stores.sort(key=lambda store: store.get("name", "").casefold())
    unified = build_unified_view(stores)
    return render_template(
        "dashboard.html",
        stores=stores,
        unified=unified,
        merged_offers=sum(entry["offer_count"] for entry in unified),
        updated_at=stock.get("updated_at"),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
