#!/usr/bin/env python3
"""Serve a local dashboard for the Pokémon TCG stock monitor."""

import json
import os
import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, render_template


app = Flask(__name__)
STOCK_FILE = Path(
    os.environ.get(
        "STOCK_FILE",
        str(Path(__file__).with_name("stock.json")),
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


app.jinja_env.filters["local_datetime"] = format_local_datetime
app.jinja_env.filters["series"] = identify_series


@app.get("/")
def dashboard():
    stock = load_stock()
    stores = sorted(
        stock["stores"].values(),
        key=lambda store: store.get("name", "").casefold(),
    )
    return render_template(
        "dashboard.html",
        stores=stores,
        updated_at=stock.get("updated_at"),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
