# Moniteur de réassort Pokémon TCG

Le moniteur consulte uniquement les catalogues publics des boutiques
configurées : Shopify `/products.json`, WooCommerce Store API, les pages HTML
publiques configurées de Philibert et Ultra-Jeux, ainsi que les données Product
JSON-LD. Il n'utilise ni Chromium, Selenium, ni Playwright et ne tente pas de
contourner les refus d'accès. Pour chaque produit Pokémon TCG scellé, il
vérifie sa disponibilité dans le flux ou le balisage public ; les fiches
disponibles nouvellement détectées déclenchent une alerte console et, si
configuré, Discord ou Telegram.

## Configurer les boutiques

Ajoutez les URL des boutiques Shopify dans le fichier `.env` du projet sur le
Raspberry Pi. Séparez plusieurs boutiques avec des virgules :

```dotenv
SHOPIFY_STORES=https://boutique1.example,https://boutique2.example
```

Le moniteur ajoute `/products.json?limit=250` à chaque domaine par défaut.
Vous pouvez régler la limite pour une boutique dans `products.json` lorsque son
endpoint renvoie une réponse trop volumineuse :

```json
{
  "stores": [
    {
      "name": "Nom de la boutique",
      "url": "https://boutique.example",
      "product_limit": 100,
      "collection": "pokemon-officiel",
      "platform": "shopify"
    }
  ]
}
```

Les boutiques Shopify de `.env` et les sources déclarées dans `products.json`
peuvent être combinées ; un domaine déjà configuré n'est interrogé qu'une fois.
Configurez uniquement les domaines dont l'endpoint JSON est public.
La limite est plafonnée à 250 produits par appel ; les produits au-delà de
cette première page ne seront pas visibles par ce moniteur. Pika-Boutique est
configurée avec `product_limit: 100` car son flux de 250 produits dépasse la
taille maximale de réponse autorisée par le script.
Une boutique peut indiquer facultativement le handle d'une collection avec
`collection` : le moniteur interroge alors
`/collections/<handle>/products.json` au lieu du catalogue général. Cela peut
permettre de suivre une sélection pertinente qui n'apparaît pas dans les
250 premiers produits du catalogue. LorenzZone utilise ainsi la collection
`produits-officiels-pokemon`. Cette source a son propre état initial ; elle
enregistre les disponibilités de départ sans envoyer d'alertes rétroactives.

Les sources WooCommerce utilisent leur Store API publique. Cards Hunter expose
`/wp-json/wc/store/v1/products`; sa catégorie de produits scellés Pokémon est
filtrée par l'identifiant 221, avec un maximum de 100 produits par cycle :

```json
{
  "name": "Cards Hunter",
  "url": "https://www.cardshunter.fr",
  "platform": "woocommerce",
  "product_limit": 100,
  "product_category_ids": [221]
}
```

Le moniteur convertit les prix WooCommerce depuis les unités mineures
(centimes) en euros et utilise `is_in_stock`, les catégories, les tags et les
images fournis dans le catalogue.

### Parser un flux affilié XML ou CSV

La fonction `parse_affiliate_product_feed(source, feed_format=None)` de
`monitor.py` permet de parser localement les exports produit XML ou CSV, par
exemple un flux Awin fourni par un partenaire. Elle renvoie une liste de
produits avec `name`, `price` (`Decimal`), `stock` (`True`, `False` ou `None`
si le flux ne précise pas le stock) et `affiliate_url`. Les noms de colonnes
usuels Awin, tels que `product_name`, `search_price`, `in_stock` et
`merchant_deep_link`, sont reconnus. Le format est détecté automatiquement ;
il peut aussi être indiqué avec `feed_format="xml"` ou `"csv"`.

La fonction lit un fichier local ou un contenu fourni et n'effectue pas elle-
même de requête réseau. Exemple avec le fixture synthétique
`sample_feed.xml` :

```sh
python3 - <<'PY'
from pathlib import Path
from monitor import parse_affiliate_product_feed

for product in parse_affiliate_product_feed(Path("sample_feed.xml")):
    print(product)
PY
```

Les boutiques génériques peuvent être ajoutées dans `shops.json` avec `name`
et `url`. Par défaut, `source_mode: "auto"` tente dans l'ordre un catalogue
Shopify public, l'API Store publique de WooCommerce, puis les données Product
JSON-LD de la page d'accueil. Ces accès sont limités aux interfaces HTTPS
publiques standards du domaine configuré ; aucune protection n'est contournée.
Les produits scellés Pokémon disponibles sont normalisés vers le même modèle
et réutilisent le cycle de détection et d'alertes. Le dashboard indique la
source détectée ou qu'aucune source standard n'a été trouvée.

Un flux XML/CSV Awin autorisé peut être déclaré avec `feed_url` et,
facultativement, `feed_format` (`auto`, `xml` ou `csv`). Quand cette URL
HTTPS est renseignée, le parseur partagé traite le flux à la place de la
découverte automatique.

Exemple :

```json
{
  "name": "Boutique Exemple",
  "url": "https://boutique.example",
  "source_mode": "auto",
  "feed_url": null,
  "feed_format": "auto"
}
```

BrazStore, Parkage et PokeBase utilisent ce mode générique. Il ne garantit pas
que chaque boutique publie un catalogue compatible ; si les endpoints standards
et les métadonnées publiques ne fournissent aucun produit, le dashboard affiche
« Aucune source publique standard détectée ». Une URL Awin réellement attribuée
reste nécessaire pour utiliser un flux affilié.

Ultra-Jeux est configuré avec `platform: "ultrajeux_html"` et une liste
`catalog_urls` limitée à ses catégories publiques de produits Pokémon scellés
(displays, blisters, ETB, coffrets, Pokébox et mini-tins). Le parseur lit les
titres, prix et libellés de disponibilité des fiches de ces pages, puis les
transmet au même filtre Pokémon scellé et au même système d'alertes que les
autres sources. Pour une boutique utilisant cette structure, `catalog_urls`
doit contenir des URL HTTPS du même hôte que la boutique.

Kairyu et PokeStation exposent également `/products.json` en Shopify. Kairyu
est interrogé jusqu'à 250 fiches (une fiche scellée disponible était présente
lors de la dernière vérification) ; PokeStation est limité à 100 fiches et
filtré ensuite par les règles de produits Pokémon scellés.

Philibert n'expose pas de catalogue JSON compatible, mais sa page publique
Pokémon renvoie directement des fiches, prix et libellés de disponibilité. La
configuration `platform: "philibert_html"` surveille cette seule page, avec un
User-Agent qui identifie le moniteur ; elle n'accède à aucune page refusée ou
protégée. Les prix de ses fiches sont extraits du balisage de la page ; le
libellé `product.oos` est traité comme une rupture. Une vérification récente a
reconnu 36 produits scellés, tous en rupture au moment du contrôle.

Le filtre garde les produits dont le titre, le type ou les tags indiquent
Pokémon et un produit scellé (booster, display, ETB, coffret, tin, blister,
etc.). Les mots-clés sont comparés comme des termes complets pour éviter, par
exemple, que « tin » dans « destinées » soit pris pour une mini-tin. Les cartes
à l'unité identifiées comme telles et accessoires connus sont exclus. Ajustez
les termes dans `monitor.py` si une boutique emploie d'autres intitulés.

## Comparer au prix retail ou à une estimation

Le flux Shopify ne fournit pas nécessairement le prix conseillé officiel.
Pour une référence officielle vérifiée, ajoutez le prix par boutique et par
fiche dans `retail_prices` de `products.json` :

```json
{
  "shopify_stores": [],
  "retail_prices": [
    {
      "store": "www.dracaugames.com",
      "handle": "pokemon-coffret-exemple",
      "price": "59.99"
    }
  ]
}
```

`store` doit correspondre exactement au nom d'hôte de la boutique configurée,
et `handle` à la partie qui suit `/products/` dans l'URL de la fiche. Le prix
est exprimé en euros. Une référence exacte par fiche prend toujours le dessus
sur l'estimation de catégorie.

Le tableau `retail_estimates` contient des fourchettes indicatives par type de
produit. À défaut d'une référence exacte, le script classe le produit d'après
son titre, affiche la fourchette comme **estimation** et calcule l'écart par
rapport au milieu de cette fourchette. Le calcul suit
`(prix boutique - prix de référence) / prix de référence × 100` et le
pourcentage affiché est arrondi à deux décimales, avec un signe `+` ou `-`.
Le dashboard affiche également l'écart absolu en euros avec son signe.
Quand la référence est une estimation, le pourcentage est exact par rapport à
sa base de calcul, mais ne constitue pas une comparaison à un prix officiel.
Les estimations ne sont pas des prix officiels vérifiés : les valeurs présentes
sont des bases de départ indicatives et doivent être confirmées ou modifiées
avant d'être utilisées pour comparer un produit particulier. Les catégories
non reconnues restent « Non vérifié » et aucun pourcentage n'est calculé.
Les règles incluent les boîtes japonaises explicitement marquées « japonais /
Japanese / Japan / JPN » avec 30 boosters (base indicative 35 €), et les
displays chinois explicitement marqués « chinois / Chinese / China » avec
15 boosters (base indicative 30 €). Ces deux valeurs sont les conversions
indicatives fournies pour cette configuration, et non une garantie de MSRP
officiel. Les règles plus spécifiques par langue et format sont évaluées avant
les catégories génériques.

Le champ Shopify `compare_at_price` n'est pas assimilé à un prix retail
officiel. Pour ajouter une catégorie, utilisez par exemple :

```json
{
  "category": "display_36",
  "label": "Display 36 boosters",
  "minimum": "160.00",
  "maximum": "180.00"
}
```

### Estimation dynamique via eBay (facultatif)

Pour les produits sans prix configuré ni prix conseillé explicite dans les
métadonnées Shopify, le moniteur peut interroger l'API officielle eBay Browse
avec le titre exact du produit. Il faut créer une application développeur eBay
et définir `EBAY_CLIENT_ID` et `EBAY_CLIENT_SECRET` dans `.env` sur le Pi. Ne
partagez pas ces identifiants. Il n'existe pas d'API publique gratuite et
générique donnant les prix conseillés Amazon, Cdiscount ou Fnac à partir d'un
nom seul ; le moniteur n'essaie donc pas de contourner leurs protections ni de
présenter des prix de vendeurs tiers comme des MSRP.

La source renvoie des annonces fixes actuellement actives sur eBay France ; le
moniteur exige au moins trois annonces dont le titre correspond suffisamment,
puis calcule leur moyenne des prix d'article en EUR (hors livraison). Ce chiffre
est affiché comme **moyenne marché eBay**, jamais comme prix retail officiel ni
comme moyenne des ventes conclues. Les résultats sont mis en cache 24 heures
(les recherches sans correspondance, 6 heures) dans
`/data/market_price_cache.json`, pour limiter les appels API. L'ordre de
priorité est : prix configuré, prix conseillé explicite, résultat eBay, puis
estimation par format. Sans identifiants eBay, les estimations locales
continuent de servir de fallback.

Les fourchettes locales sont indicatives, pas des MSRP vérifiés : display
standard (180 € ; tout titre contenant « display » est classé dans cette règle,
même sans nombre de boosters), ETB (54,99–59,99 €), coffret (44,99–54,99 €),
Tin Cube/Pokébox (24,99–25,90 €), Mini Tin (10,99–11,99 €), Duopack/Tripack
(11,99–13,50 €), bundle de 6 boosters (29,99 €), display chinois de 15
boosters (30 €) et boîte japonaise de 30 boosters (35 €). Quand aucune source
explicite, aucun résultat Web exploitable et aucune règle de format ne
s'appliquent, le prix reste « Non vérifié » plutôt que d'inventer une valeur.

L'API Browse requiert un jeton OAuth d'application :
[documentation officielle eBay Browse](https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search)
et [client credentials OAuth](https://developer.ebay.com/api-docs/static/oauth-client-credentials-grant.html).
Il n'existe pas de flux public Google Shopping donnant librement les MSRP des
produits scellés. Les API TCG publiques axées sur les prix concernent surtout
les cartes individuelles, pas les boîtes scellées.

Une boutique qui ne publie pas d'endpoint Shopify `/products.json` ne peut pas
être ajoutée par cette méthode. Les domaines des grands revendeurs qui
renvoyaient des erreurs HTTP 403 ont été retirés : aucun contournement n'est
tenté.

## Fréquence et limites de débit

Dans Portainer, le cycle est configuré à **60 secondes au minimum**. Le script
respecte les réponses HTTP 429/503 et `Retry-After` lorsqu'elles sont fournies,
mais un endpoint public peut tout de même appliquer des limites ou refuser les
requêtes. Une réponse 403 n'est pas contournée. Si une boutique indique une
limite plus élevée, utilisez une fréquence plus lente.

Les requêtes utilisent `User-Agent: Mozilla/5.0` et `Accept: application/json`.
Les boutiques sont espacées de quelques secondes pour réduire les appels
simultanés.

## Alertes et état

Le premier relevé d'une boutique crée une référence sans alerte initiale. Aux
cycles suivants, un nouveau produit disponible déclenche une notification.
L'alerte comprend le lien direct de la fiche et, si l'API fournit une image,
une image intégrée dans Discord ou une photo Telegram. Dans Discord, l'alerte
utilise un embed épuré : nom du produit cliquable, boutique, prix constaté,
lien d'achat et grande image facultative, sans détails de diagnostic. Le prix
minimum des variantes disponibles est affiché (avec « À partir de » si leurs
prix diffèrent). Le mode de test affiche clairement « Test » et n'affirme pas
qu'un nouvel arrivage vient d'être détecté. Telegram conserve le détail retail,
écart et quantité dans le message texte.

La quantité du dashboard ou de Telegram n'est remplie que si le flux JSON
public la publie explicitement dans `inventory_quantity`, `available_quantity`
ou `quantity`, au niveau des variantes disponibles ou du produit. Le moniteur
ne charge pas les pages HTML produit pour récupérer un texte tel que « X
articles » et ne contourne pas les refus 403, Cloudflare ou autres protections.
Une quantité non exposée reste « Non communiquée » plutôt que d'être devinée.

Pour Discord, conservez `DISCORD_WEBHOOK_URL` dans `.env`. Le script lit cette
variable au démarrage ; ne la partagez pas et ne l'inscrivez pas dans
`products.json`. Le fichier `.env` est exclu de Git et du contexte de build
Docker ; `.env.example` ne contient que des noms de variables et des valeurs
vides. Les permissions locales recommandées pour `.env` sont `600`.
N'ajoutez pas de secrets dans `monitor.py`, `config.py` ou les fichiers de
configuration suivis. Si un webhook ou un token a déjà été publié, révoquez-le
et générez-en un nouveau. Le test existant reste disponible :

```sh
python3 monitor.py --test-discord
```

Telegram reste facultatif via `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID`.
L'état persistant est stocké dans `/data/search_state.json` par le volume
Docker `pokemon-restock-state`.

## Tableau de bord web local

À chaque cycle, le moniteur enregistre les produits disponibles dans
`/data/stock.json` : boutique, nom, URL, image, prix boutique, prix retail ou
estimation, écart signé et quantité si la boutique la communique. Le relevé
réussi le plus récent de chaque boutique est conservé si un autre flux est
temporairement indisponible. Le service Flask lit ce fichier et sert le
tableau de bord sur le port 5000 ; la page se rafraîchit automatiquement.
L'interface utilise une grille de cartes adaptative, un thème sombre et de
grandes images de produits pour les identifier rapidement. La barre de
recherche filtre instantanément les produits de toutes les boutiques par nom,
boutique ou série reconnue dans le titre ; les séries inconnues sont indiquées
comme « Série à identifier ». La détection utilise désormais le titre, le type,
le fournisseur, les tags Shopify, le handle produit ainsi que les textes ALT et
les noms d'image/URL quand la boutique les fournit. Elle reconnaît notamment
les variantes « 30 ans », « 30th Anniversary », « Celebration(s) » et
« Mid-Autumn »/« Moon Festival ». Les images ne sont pas analysées visuellement ;
seuls leurs métadonnées et noms accessibles sont utilisés. Les dates du relevé
sont affichées en heure française, au format jour/mois/année et heure-minute,
sans secondes.

Après reconstruction et redéploiement de la Stack, ouvrez
`http://<adresse-du-Raspberry-Pi>:5000` depuis votre réseau local. Le port est
publié sur les interfaces réseau de l'hôte ; limitez son accès avec le
pare-feu du Raspberry Pi si nécessaire. Flask est lancé sans mode debug.
Pour exécuter l'interface hors Docker, installez `requirements.txt`, puis
lancez `python3 app.py`. Par défaut, il lit `stock.json` à côté de `app.py` ;
vous pouvez préciser le chemin avec `STOCK_FILE`.

## Déploiement Portainer

1. Ajoutez vos URL Shopify à `.env` sur le Raspberry Pi.
2. Reconstruisez l'image pour installer Flask :

   ```sh
   cd /home/lacanette/pokemon-restock
   docker build -t pokemon-restock:local .
   ```

3. Dans Portainer, redéployez la Stack `pokemon-restock` et vérifiez les logs
   des conteneurs `pokemon-restock` et `pokemon-restock-dashboard`.

Le code, `.env` et `products.json` sont montés en lecture seule. La variable
`SHOPIFY_STORES` est lue depuis `.env` au démarrage du conteneur.

## Accès distant persistant avec ngrok

Le service `dashboard-tunnel` est préparé dans Docker Compose avec le profil
`tunnel` et redémarre automatiquement après une panne du conteneur. Il n'est
pas démarré par défaut. Le domaine affiché dans l'assistant ngrok
(`tweed-popular-pledge.ngrok-free.dev`) est configuré par défaut. Ajoute le
jeton privé fourni par ngrok dans le fichier `.env` local du Raspberry Pi,
sans le partager ni le committer :

```dotenv
NGROK_AUTHTOKEN=jeton_fourni_par_ngrok
```

Si l'assistant ngrok affiche un autre domaine pour le compte, ajoute aussi
`NGROK_DOMAIN=ton-domaine.ngrok-free.dev`. Démarre ensuite le tunnel depuis le
dossier du projet :

```sh
docker compose --profile tunnel up -d dashboard-tunnel
docker compose logs --tail=30 dashboard-tunnel
```

L'URL prévue avec le domaine de la capture est
`https://tweed-popular-pledge.ngrok-free.dev`. Le tunnel ne pourra la servir
qu'après configuration locale du jeton et validation de ce domaine par ngrok.

Pour l'arrêter : `docker compose --profile tunnel stop dashboard-tunnel`.
Cette exposition est publique et ne demande pas de connexion au dashboard ;
toute personne qui connaît l'adresse peut consulter les produits et les liens
marchands. L'option gratuite ne garantit pas une disponibilité continue : le
redémarrage automatique aide à rétablir le tunnel, mais dépend de l'accès
Internet, du compte et du service ngrok.
