import json
from pathlib import Path

STATE_FILE = "stock_state.json"

def load_seen_stocks():
    """Charge la liste des produits déjà vus/alertés."""
    path = Path(STATE_FILE)
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()

def save_seen_stocks(seen_set):
    """Sauvegarde la liste des produits vus."""
    path = Path(STATE_FILE)
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(list(seen_set), f)
    except Exception:
        pass