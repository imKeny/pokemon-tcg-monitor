# MEMO POKESNIPE - NE PAS OUBLIER

RAPPORT TECHNIQUE DE CRISE & MÉMO DE RÉFÉRENCE - POKESNIPE
1. Contexte et Historique du Projet
Nom du projet : PokeSnipe (anciennement PokeSnip), un système de surveillance multi-plateforme automatisé pour le suivi des stocks Pokémon (Shopify, PrestaShop, WooCommerce, et catalogues HTML génériques comme Parkage, Philibert, Ultra-Jeux, etc.).

Infrastructure : Raspberry Pi, Docker, Docker Compose, Portainer.

Dossier de travail principal : /home/lacanette/pokemon-restock

Objectif initial : Avoir un bot stable, s'appuyant sur un ensemble de 19+ boutiques actives, routant les alertes par langue/pays vers des webhooks Discord avec des identités de bots personnalisées, et alimentant un dashboard Flask.

2. Chronologie de la Crise (Les 2-3 heures de galère)
Pendant cette session, plusieurs erreurs bloquantes se sont enchaînées à cause de configurations incorrectes :

L'erreur de syntaxe Python ([Errno] SyntaxError: unterminated string literal) :

Cause : Une chaîne de caractères mal formatée dans un log d'erreur Discord (LOG.error(fErreur d'envoi Discord : {e}) au lieu d'utiliser des guillemets f"...").

Conséquence : Le script plantait instantanément au démarrage avec une erreur de syntaxe en boucle, empêchant le conteneur de tourner.

L'erreur critique de système de fichiers en lecture seule ([Errno 30] Read-only file system: 'search_state.json') :

Cause : Dans le fichier docker-compose.yml, le volume principal du projet était monté avec la contrainte de lecture seule (:ro) : - /home/lacanette/pokemon-restock:/app:ro.

Conséquence : Le script Python, configuré pour s'exécuter dans le répertoire de travail /app, essayait d'écrire et de mettre à jour l'état de recherche (search_state.json), ce que Docker bloquait net, provoquant un blocage persistant de la sauvegarde de l'état.

Le problème d'extraction des plateformes non-Shopify :

Cause initiale : Les extracteurs pour PrestaShop, WooCommerce et les sites HTML génériques renvoyaient des tableaux vides (return []) pour éviter les plantages.

Résolution : Nous avons dû implémenter des fonctions d'analyse robustes pour que des boutiques comme Parkage et Pikastore parviennent enfin à remonter leurs produits (ex: 4 produits pour Parkage, 12 pour Pikastore), tandis que Shopify tournait déjà à plein régime avec ses 250 produits par catalogue.

3. La Solution Définitive ("Gold Standard")
Pour débloquer la situation de manière définitive, voici ce qui a été corrigé et validé :

A. Le docker-compose.yml corrigé (Crucial)
Il a fallu impérativement retirer le :ro du volume principal pour que le conteneur puisse écrire ses états :

YAML
services:
  pokemon-restock:
    image: pokemon-restock:local
    container_name: pokemon-restock
    working_dir: /app
    command:
      - python
      - -u
      - main.py
      - --config
      - /app/products.json
      - --shops
      - /app/shops.json
      - --state
      - /data/search_state.json
      - --stock
      - /data/stock.json
      - --market-cache
      - /data/market_price_cache.json
      - --interval
      - "60"
    restart: unless-stopped
    volumes:
      - /home/lacanette/pokemon-restock:/app   # <--- PAS DE ":ro" ICI !
      - pokemon-restock-state:/data
B. Commandes de maintenance et de secours validées
Redémarrage propre des conteneurs :

Bash
cd ~/pokemon-restock && docker compose down && docker compose up -d --build
Création de la sauvegarde physique (stockée dans le dossier parent) :

Bash
tar -czvf ../backup-PokeSnipe.tar.gz .
Tag Git de référence : PokeSnipe

Voilà, ce rapport est gravé ici. Si tu me le recopies-colles à l'avenir, je comprendrai instantanément l'architecture, l'historique des pannes (surtout le piège du :ro sur le volume Docker et la syntaxe des f-strings) et je ne te ferai plus perdre ton temps. Est-ce que la punition te paraît acceptée ?