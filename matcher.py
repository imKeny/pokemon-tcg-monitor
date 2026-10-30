import unicodedata
import re

def normalize(text):
    """Normalise un texte (minuscules, suppression des accents et caractères spéciaux)."""
    if not isinstance(text, str):
        return ""
    text = text.lower()
    text = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
    return text

def contains_product_term(normalized_title, term):
    """Vérifie si un terme de recherche correspond au titre du produit de manière intelligente."""
    norm_term = normalize(term)
    if not norm_term:
        return False
    
    pattern = r'\b' + re.escape(norm_term) + r'\b'
    if re.search(pattern, normalized_title):
        return True
        
    if norm_term in normalized_title:
        return True
        
    return False

def match_product(title, products_config):
    """Détermine si un produit correspond aux critères et renvoie ses détails (ou True par défaut)."""
    normalized_title = normalize(title)
    
    # Si products_config est une liste de dictionnaires ou de mots-clés
    if isinstance(products_config, list):
        for item in products_config:
            if isinstance(item, dict):
                term = item.get("name", "") or item.get("term", "")
                if term and contains_product_term(normalized_title, term):
                    return item  # Renvoie l'objet complet (avec sa langue potentielle)
            elif isinstance(item, str):
                if contains_product_term(normalized_title, item):
                    return {"name": item, "lang": "FR"}  # Valeur par défaut
                    
    # Si products_config est un dictionnaire
    elif isinstance(products_config, dict):
        for term, details in products_config.items():
            if contains_product_term(normalized_title, term):
                if isinstance(details, dict):
                    details["name"] = term
                    return details
                return {"name": term, "lang": "FR"}
                
    # Par défaut, si aucun produit spécifique n'est configuré, on cherche "pokemon"
    if not products_config and contains_product_term(normalized_title, "pokemon"):
        return {"name": "pokemon", "lang": "FR"}
        
    return None