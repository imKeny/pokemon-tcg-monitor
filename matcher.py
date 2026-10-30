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
    
    # Utilisation d'expressions régulières pour les mots entiers ou correspondances propres
    pattern = r'\b' + re.escape(norm_term) + r'\b'
    if re.search(pattern, normalized_title):
        return True
        
    # Fallback si le terme est contenu directement
    if norm_term in normalized_title:
        return True
        
    return False

def match_product(title, products_config):
    """Détermine si un produit correspond aux critères de recherche configurés."""
    normalized_title = normalize(title)
    
    if isinstance(products_config, list):
        for term in products_config:
            if isinstance(term, str) and contains_product_term(normalized_title, term):
                return True
    elif isinstance(products_config, dict):
        for term in products_config.keys():
            if contains_product_term(normalized_title, term):
                return True
                
    # Par défaut, si aucun produit spécifique n'est configuré, on cherche "pokemon"
    if not products_config and contains_product_term(normalized_title, "pokemon"):
        return True
        
    return False