# Boutiques rejetées

| Boutique | Raison technique |
|---|---|
| fuji-store.fr | WooCommerce, mais l'API Store (`/wp-json/wc/store/v1/products`) répond HTTP 403 (WAF Apache). `/wp-json/wp/v2/product` est public mais n'expose ni prix ni stock. |
| maxicartes.com | API Store WooCommerce accessible, mais seulement 58 produits, aucun Pokémon (Dragon Ball Super, Naruto Kayou). |
| auxtroiskoalas.fr | Site Odoo (nginx) : ni `/products.json` ni API Store WooCommerce, aucun endpoint JSON public de catalogue. |
