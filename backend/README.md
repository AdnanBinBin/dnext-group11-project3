# DNEXT — Mirror Matching (Gold) & Backend API

> Projet de Master **Cloud-Native Trade Anomaly Detection & Reporting Platform**  
> Ce dossier contient **la couche Gold et le backend de consultation**, et complète `data-platform/` (Bronze → Silver). Il est rédigé pour qu'un membre de l'équipe puisse comprendre, utiliser et redéployer les composants sans être spécialiste d'AWS.
>
> **Intégration au dépôt commun** : la configuration vient de [`../config.env`](../config.env). Depuis la racine, `./scripts/deploy.sh gold api` crée la Gold (étape 2 ci-dessous automatisée par [`scripts/build_gold.sh`](scripts/build_gold.sh)) puis l'API. Les routes `/api/v1/reports` des rapports IA sont ajoutées à cette même API par la stack `dnext-trade-reports` (voir [`../reports/README.md`](../reports/README.md)) ; c'est pourquoi le CORS autorise aussi `POST`.

## 1. Par où commencer ?

**Tu développes le frontend Vue.js ?** Tu n'as normalement **rien à déployer** : utilise la section [6. Intégrer l'API dans Vue.js](#6-intégrer-lapi-dans-vuejs). Le navigateur parle à l'API, **jamais directement à Athena ou à S3**.

**Tu reprends le backend ?** Lis les sections 2 à 5 pour comprendre les données, puis les sections 7 à 9 pour les tests et le déploiement.

**Tu t'occupes des rapports IA ?** L'API fournit les `anomaly_id` et les détails d'une anomalie. Le lancement et le suivi des rapports IA sont dans `reports/` : `POST /api/v1/reports` avec un `anomaly_id`, sur la même API.

### État observé sur AWS

L'environnement ci-dessous a été testé sur AWS **le 20 octobre 2026** ; la présence de la table Gold dans Glue a été recontrôlée le **22 octobre 2026**. Ces chiffres sont un état observé, pas une promesse de disponibilité permanente.

| Élément | Valeur |
|---|---|
| Compte AWS partagé | `352909266144` |
| Région | `eu-north-1` (Stockholm) |
| Base Glue / Athena | `dnext_trade` |
| Table d'entrée Silver | `trade_flows` |
| Table de sortie Gold | `mirror_matches` |
| Workgroup Athena | `dnext-trade-wg` |
| Lambda de consultation | `dnext-trade-backend-api` |
| Stack backend | `dnext-trade-backend` |
| **URL de base de l'API** | **`https://0wq8txu0c8.execute-api.eu-north-1.amazonaws.com`** |

Vérification la plus simple (depuis un terminal ou le navigateur) :

```bash
curl -i 'https://0wq8txu0c8.execute-api.eu-north-1.amazonaws.com/api/v1/health'
```

Réponse observée : HTTP `200`, `{"status":"ok","service":"dnext-backend"}`.

---

## 2. Comment ce backend se raccorde à la Silver

Le projet est volontairement réparti en deux parties indépendantes :

```text
Fichier UN Comtrade (Excel)
       |
       v
S3 Bronze / Raw
       |
       v
Lambda d'ingestion (projet dnext-data-platform)
       |
       v
S3 Silver : données nettoyées au format Parquet
       |
       +--> Glue : dnext_trade.trade_flows
                    |
                    |  Athena : mirror matching (calcul initial)
                    v
                 S3 Gold : résultats Parquet
                    |
                    +--> Glue : dnext_trade.mirror_matches
                                   ^
                                   | Athena : SELECT / filtrage / agrégation
                                   |
Frontend Vue.js <-- HTTPS --> API Gateway <---> Lambda backend (src/app.py)
```

### Qui fait quoi ?

| Composant | Responsabilité | Projet / propriétaire |
|---|---|---|
| **Bronze** (`dnext-trade-raw-...`) | Conserver l'Excel original | `dnext-data-platform` |
| **Lambda d'ingestion** | Valider, nettoyer et convertir l'Excel en Parquet | `dnext-data-platform` |
| **Silver** (`dnext-trade-silver-...`) | Conserver les déclarations nettoyées ; table `dnext_trade.trade_flows` | `dnext-data-platform` |
| **Athena, calcul Gold** | Associer les déclarations Import/Export et calculer les anomalies | **Ce dépôt** : `sql/02_populate_gold.sql` |
| **Gold** (`dnext-trade-gold-...`) | Conserver les résultats précalculés ; table `dnext_trade.mirror_matches` | **Ce dépôt** |
| **API Gateway + Lambda** | Servir les résultats Gold au frontend sous forme de JSON | **Ce dépôt** |
| **Frontend Vue.js** | Afficher les filtres, la heatmap et les détails | Dépôt / composant frontend |

**S3 stocke les fichiers**, **Glue décrit les tables et leurs colonnes**, **Athena exécute le SQL**. Il ne faut pas chercher un bucket S3 parmi les *tables* Athena : dans Athena, sélectionner la base `dnext_trade` et la table `mirror_matches`.

Autre distinction : `dnext-trade-athena-<account-id>` est le bucket **technique des résultats de requêtes Athena** (rétention courte configurée par la Silver). Ce n'est **pas** le bucket métier Gold, qui conserve les anomalies précalculées.

### Quelles colonnes de la Silver utilisons-nous ?

La table `trade_flows` contient une ligne **par déclaration douanière**. Une même expédition peut apparaître deux fois : vue par l'exportateur (`Export`, valeur FOB) et vue par l'importateur (`Import`, valeur CIF). Ces valeurs ne doivent **pas** être additionnées comme s'il s'agissait de deux expéditions.

Les colonnes utiles au calcul : `year_month`, `exporter_iso3`, `importer_iso3`, `hs4`, `product`, `flow`, `price_basis`, `value_usd`, `qty_t`, `partner_is_aggregate`. Les colonnes `exporter_iso3` et `importer_iso3` ont déjà été normalisées par la Silver, quel que soit le pays déclarant.

Le rapprochement utilise cette clé commune :

```text
(year_month, exporter_iso3, importer_iso3, hs4)
```

Exemple : une déclaration `Export` ARG → BRA et une déclaration `Import` BRA depuis ARG, sur janvier 2023 et HS4 `1001`, forment une paire à comparer.

---

## 3. Ce que calcule la Gold

Le fichier `sql/02_populate_gold.sql` rapproche les déclarations avec un **`FULL OUTER JOIN`**. Contrairement à un `INNER JOIN`, cela permet aussi de conserver les déclarations qui n'ont pas de miroir.

Pour une paire disposant de valeurs et de quantités positives :

```text
prix FOB (USD/t) = valeur export USD / quantité export t
prix CIF (USD/t) = valeur import USD / quantité import t
gap (%)          = 100 × (prix CIF / prix FOB − 1)
```

La paire est signalée comme anormale si **CIF < FOB** (`CIF_BELOW_FOB`) ou si **l'écart dépasse strictement 35 %** (`GAP_ABOVE_35`). Les prix ne sont pas arrondis avant la classification.

### Statuts d'une ligne Gold

| `match_status` | Signification |
|---|---|
| `MATCHED` | Les deux déclarations existent et les prix sont calculables |
| `MISSING_EXPORT` | La déclaration d'exportation manque |
| `MISSING_IMPORT` | La déclaration d'importation manque |
| `INVALID_PRICE` | Prix non calculable (quantité ou valeur nulle / invalide) |
| `AGGREGATE_PARTNER` | Partenaire agrégé, non comparable à un pays individuel |

La colonne `is_anomaly` n'est vraie que pour une paire `MATCHED` qui franchit une règle. **Une déclaration sans miroir n'est pas considérée comme « cohérente » : c'est une absence d'information.**

Une ligne Gold correspond à une clé flux/mois/produit. L'`anomaly_id` est un identifiant déterministe de 32 caractères hexadécimaux calculé à partir de cette clé ; c'est ce que l'API et, plus tard, le workflow IA peuvent utiliser pour retrouver le même cas.

**Résultats observés après l'ingestion initiale :** 142'451 lignes Gold, 142'451 identifiants distincts, 27'467 anomalies. Répartition des statuts : `MATCHED` 57'355, `INVALID_PRICE` 781, `MISSING_EXPORT` 44'923, `MISSING_IMPORT` 37'762, `AGGREGATE_PARTNER` 1'630.

### Quand ce calcul est-il exécuté ?

**Une fois après l'ingestion initiale, pour le MVP.** Il n'est **pas** exécuté lorsqu'on affiche la heatmap, lorsqu'on change un filtre ou lorsqu'on clique sur une cellule. À chaque demande du frontend, la Lambda lance uniquement des requêtes **de lecture et d'agrégation sur Gold** avec Athena.

`monthly`, `quarterly` et `yearly` indiquent la **granularité temporelle affichée**, pas une fréquence de recalcul planifiée. Il n'y a actuellement **ni tâche journalière, ni recalcul automatique après ingestion**.

> **Attention aux doublons :** `sql/02_populate_gold.sql` contient `INSERT INTO`, qui **ajoute** des données. Ne le relancez pas sur une table déjà alimentée : il créerait des doublons. Voir la section 9 pour les reconstructions.

---

## 4. À quoi sert chaque fichier du dépôt ?

```text
dnext-backend-mvp/
├── README.md                  # Ce guide : architecture, utilisation, déploiement
├── openapi.yaml               # Contrat HTTP pour le frontend et les autres équipes
├── infra/
│   ├── gold-storage.yaml      # Crée le bucket S3 Gold (CloudFormation)
│   └── backend-api.yaml       # Crée Lambda, API Gateway, rôle IAM, routes et CORS
├── sql/
│   ├── 01_create_gold.sql     # Déclare la table Gold dans Glue via Athena
│   ├── 02_populate_gold.sql   # Calcule et insère les résultats du mirror matching
│   └── 03_validate_gold.sql   # Vérifie les résultats Gold après calcul
├── src/
│   └── app.py                 # Code exécuté par la Lambda à chaque appel HTTP
├── scripts/
│   ├── build_gold.sh          # Crée le bucket et la table Gold, les remplit une seule fois
│   └── deploy_api.sh          # Empaquette, transfère et déploie l'API sur AWS
└── tests/
    └── test_app.py            # 7 tests unitaires locaux, sans appels AWS réels
```

| Fichier | Concrètement, que fait-il ? | Pourquoi existe-t-il ? |
|---|---|---|
| `infra/gold-storage.yaml` | Crée le bucket `dnext-trade-gold-<account-id>`, chiffré et non public. | Stockage durable et indépendant de la Silver. Le bucket est configuré pour être conservé lors d'une suppression de stack (`Retain`). |
| `sql/01_create_gold.sql` | Déclare `dnext_trade.mirror_matches` (colonnes, types et emplacement S3 Parquet). | Athena doit connaître le schéma avant de pouvoir insérer / lire les résultats. |
| `sql/02_populate_gold.sql` | Fait la jointure Import/Export, calcule FOB/CIF/gap, les statuts et les anomalies, puis insère dans Gold. | C'est le calcul analytique métier, lancé **ponctuellement**. |
| `sql/03_validate_gold.sql` | Compte les lignes et statuts, vérifie les IDs et un exemple ARG → BRA. | Prouver que la table contient ce qui est attendu. |
| `src/app.py` | Reçoit les événements HTTP de l'API, valide les filtres, interroge **Gold** avec `boto3`, retourne du JSON. | Séparer les requêtes utilisateur du calcul analytique. |
| `infra/backend-api.yaml` | Déclare la Lambda, son rôle IAM, les variables d'environnement, API Gateway HTTP API, routes, CORS et limites de trafic. | Déploiement reproductible, sans configuration manuelle dispersée. |
| `scripts/deploy_api.sh` | Crée `backend.zip` à partir de `app.py`, l'envoie au bucket d'artefacts, déploie la stack, met à jour le code Lambda. | Une commande pour déployer / actualiser l'API. |
| `tests/test_app.py` | Teste la validation des paramètres, la heatmap simulée, la route santé et les limites de requêtes. | Détecter les régressions rapidement avant AWS. |
| `openapi.yaml` | Documente les routes, paramètres et réponses HTTP. | Contrat que le développeur Vue.js peut lire sans connaître la Lambda. |
| `README.md` | Explique le système et la procédure de reprise. | Faciliter le travail en équipe et la remise du projet. |

Le `backend.zip` éventuellement créé à la racine par `deploy_api.sh` est **un artefact généré**, pas du code à versionner dans Git. Ne jamais committer de clés AWS, fichiers `.aws/` ou environnements virtuels `.venv/`.

### Pourquoi une seule Lambda pour cinq endpoints ?

C'est un **choix volontaire pour le MVP** : l'API a peu de routes, elles font essentiellement la même chose (valider les paramètres, lire une table Athena et renvoyer du JSON), et la charge attendue est limitée.

- **Moins d'infrastructure à gérer :** un code à empaqueter, un rôle IAM, une configuration et un déploiement, plutôt que cinq fonctions à maintenir.
- **Logique réutilisable :** `app.py` centralise la connexion Athena, la validation des paramètres et le format des erreurs ; les fonctions Python restent séparées par endpoint.
- **Adapté à Lambda :** aucun serveur permanent, exécution seulement lorsqu'une requête arrive, coûts proportionnés au MVP.
- **Compromis assumé :** les routes partagent les mêmes permissions, ressources et limites de concurrence. Pour une application plus importante, on pourrait séparer certaines routes en plusieurs Lambda (par exemple, si elles nécessitaient des permissions ou des performances très différentes).

**Important :** « une seule Lambda » signifie **une seule Lambda applicative de consultation** dans ce dépôt. La **Lambda Bronze → Silver** du coéquipier est une fonction distincte. Le *mirror matching*, lui, est calculé par **Athena SQL**, sans Lambda dédiée.

De même, nous utilisons une **API Gateway HTTP API** (API HTTP, version 2) qui expose des routes de style REST, et non le produit AWS « REST API » (version 1). C'est plus simple pour ce MVP et doit être reflété dans le rapport si son schéma était générique.

---

## 5. Les cinq endpoints disponibles

**Base URL :** `https://0wq8txu0c8.execute-api.eu-north-1.amazonaws.com`

| Méthode et chemin | Utilité | Athena appelé ? |
|---|---|---|
| `GET /api/v1/health` | Vérifier que la Lambda répond | Non |
| `GET /api/v1/filters` | Liste des exportateurs, produits et fréquences | Oui |
| `GET /api/v1/heatmap` | Cellules de la matrice partenaires × périodes | Oui |
| `GET /api/v1/anomalies` | Liste filtrée des anomalies | Oui |
| `GET /api/v1/anomalies/{id}` | Détails d'un flux identifié | Oui |

Consulter `openapi.yaml` pour la liste technique des paramètres ; voici les scénarios les plus fréquents.

### A. Construire les menus de filtres

```http
GET /api/v1/filters
```

Réponse observée, abrégée :

```json
{
  "exporters": ["ARG", "AUS", "BRA", "CAN", "FRA", "KAZ", "ROU", "RUS", "UKR", "USA"],
  "products": [
    {"hs4": "1001", "name": "Wheat"},
    {"hs4": "1003", "name": "Barley"}
  ],
  "frequencies": ["monthly", "quarterly", "yearly"],
  "anomaly_threshold_pct": 35
}
```

La réponse réelle contient les **sept** produits disponibles. Utiliser les codes HS4 (`"1001"`, etc.), **pas** le libellé du produit, pour les requêtes suivantes.

### B. Afficher la heatmap

```http
GET /api/v1/heatmap?exporter=ARG&product=1001&frequency=yearly&start=2023-01&end=2023-12
```

- **Obligatoires** : `exporter` (ISO3) et `product` (HS4 à 4 chiffres).
- **Optionnels** : `frequency` (`monthly`, `quarterly`, `yearly` ; défaut `yearly`), `start` / `end` (`YYYY-MM`).
- **Résultat** : `{"exporter":"ARG","product":"1001","frequency":"yearly","cells":[...]}`.

Exemple **réel** de cellule retournée :

```json
{
  "partner": "BRA",
  "period": "2023",
  "total_count": 12,
  "matched_count": 12,
  "anomaly_count": 5,
  "status": "flagged"
}
```

**Pour afficher une cellule :**

| `status` | Sens | Suggestion visuelle |
|---|---|---|
| `flagged` | Au moins une anomalie dans la période | Rouge, cliquable |
| `consistent` | Aucune anomalie, mais au moins une paire `MATCHED` | Blanc / neutre |
| `no_match` | Aucune paire comparable pour cette cellule | Gris / données indisponibles |

La règle d'agrégation (`flagged` si au moins un mois anormal) est **une convention MVP** à valider en équipe. Une cellule `consistent` peut contenir **aussi** des mois sans miroir : comparer `matched_count` et `total_count` si nécessaire. Une cellule **absente** de `cells` ne signifie pas automatiquement `consistent` ; il peut simplement n'y avoir aucun flux pour ce partenaire et cette période.

Les dix partenaires sont sélectionnés dans le périmètre demandé selon la somme de valeur commerciale déclarée disponible, en excluant les partenaires agrégés. Les périodes sont retournées sous la forme `2023-01` (mois), `2023-Q1` (trimestre) ou `2023` (année).

### C. Lister les anomalies d'une cellule rouge

```http
GET /api/v1/anomalies?exporter=ARG&importer=BRA&product=1001&start=2023-01&end=2023-12&limit=10
```

Tous ces paramètres sont **facultatifs** pour cet endpoint ; `limit` vaut 50 par défaut et doit rester entre 1 et 100. Il n'y a **pas de pagination** dans cette version : appliquer les filtres pour limiter les résultats.

La réponse possède la forme `{"items":[...],"limit":10}`. Chaque élément contient notamment `anomaly_id`, `year_month`, `exporter_iso3`, `importer_iso3`, `hs4`, `fob_price`, `cif_price`, `gap_pct` et `anomaly_reason`.

Pour transformer un clic sur une cellule en appel `/anomalies` :

| Cellule cliquée | Paramètres à envoyer à `/anomalies` |
|---|---|
| Pays `BRA`, mois `2023-01` | `importer=BRA&start=2023-01&end=2023-01` |
| Pays `BRA`, trimestre `2023-Q1` | `importer=BRA&start=2023-01&end=2023-03` |
| Pays `BRA`, année `2023` | `importer=BRA&start=2023-01&end=2023-12` |

Ajouter aussi `exporter` et `product` de la sélection courante. **`period` n'est pas un paramètre reconnu** par `/anomalies` : il faut le convertir en intervalle de mois.

### D. Consulter le détail d'une anomalie

```http
GET /api/v1/anomalies/C671CE736AA802F36D74297BE29EE53F
```

Réponse réelle abrégée :

```json
{
  "anomaly_id": "C671CE736AA802F36D74297BE29EE53F",
  "year_month": "2025-12",
  "exporter_iso3": "ARG",
  "importer_iso3": "AGO",
  "hs4": "1001",
  "product": "Wheat",
  "fob_price": 209.8347912862542,
  "cif_price": 307.66941302589123,
  "gap_pct": 46.62459506353844,
  "match_status": "MATCHED",
  "anomaly_reason": "GAP_ABOVE_35",
  "is_anomaly": true
}
```

La réponse complète contient aussi les valeurs en USD et les quantités en tonnes. Cette route a été testée avec succès sur AWS avec un **identifiant en majuscules**. Les deux casses sont acceptées par la version corrigée de `src/app.py`.

### E. Gestion d'erreurs

- `200` : succès.
- `400` : paramètre invalide, par exemple `{"error":"product doit être un code HS4 de 4 chiffres"}`.
- `404` : route ou identifiant introuvable.
- `502` / `503` : erreur ou attente trop longue d'Athena.

---

## 6. Intégrer l'API dans Vue.js

**Aucune clé AWS dans le navigateur.** Appeler l'API HTTP avec `fetch()` ; le backend se charge d'interroger Athena avec son rôle IAM.

Exemple minimal en JavaScript (utilisable dans un composant Vue ou un module API) :

```js
const API_BASE = 'https://0wq8txu0c8.execute-api.eu-north-1.amazonaws.com';

export async function loadHeatmap(exporter, product, frequency = 'yearly') {
  const params = new URLSearchParams({
    exporter, product, frequency,
    start: '2023-01', end: '2023-12',
  });

  const response = await fetch(`${API_BASE}/api/v1/heatmap?${params}`);
  if (!response.ok) {
    throw new Error(`Erreur API : HTTP ${response.status}`);
  }
  const data = await response.json();
  return data.cells; // tableau de cellules { partner, period, status, ... }
}
```

Pour charger les menus, appeler `/api/v1/filters`. Pour détailler une cellule rouge, appeler `/api/v1/anomalies` avec le partenaire et les bornes mensuelles, puis `/api/v1/anomalies/{id}` pour un cas particulier.

**CORS** : une requête `OPTIONS` depuis un autre domaine a retourné `204` avec `Access-Control-Allow-Origin: *` et `GET,OPTIONS`. Cette configuration est prévue **pour la démonstration**, pas pour une application de production.

**Performance** : presque chaque appel GET déclenche une requête Athena et peut prendre quelques secondes. Prévoir un indicateur de chargement, éviter de relancer un appel à chaque frappe au clavier et ne pas rafraîchir la heatmap automatiquement toutes les secondes.

**Rapports IA** : `POST /api/v1/reports` avec `{"anomaly_id": "..."}`, puis `GET /api/v1/reports/{id}` jusqu'au statut `COMPLETED` ; contrat complet dans [`../reports/README.md`](../reports/README.md). Ces routes sont servies par la Lambda des rapports, pas par `src/app.py`.

---

## 7. Tester ce qui est déjà déployé (sans rien recréer)

Dans un terminal disposant de `curl` :

```bash
API='https://0wq8txu0c8.execute-api.eu-north-1.amazonaws.com'
curl -i "$API/api/v1/health"
curl -i "$API/api/v1/filters"
curl -i "$API/api/v1/heatmap?exporter=ARG&product=1001&frequency=yearly&start=2023-01&end=2023-12"
curl -i "$API/api/v1/anomalies?exporter=ARG&product=1001&limit=3"
```

Résultats réels observés le 20/10/2026 : les cinq routes GET (y compris le détail par ID) ont répondu `HTTP 200` ; un produit invalide a répondu `400` ; le préflight CORS a répondu `204`.

### Tests unitaires locaux

Dans le dossier du projet :

```bash
python3 -m unittest discover -s tests -v
```

Sept tests réussissaient lors de la validation. Ils utilisent des réponses simulées et ne nécessitent pas d'appels Athena réels. Le module Python `boto3` doit être disponible dans l'environnement local (il est inclus dans le runtime Lambda AWS ; en local, l'installer au besoin dans un environnement virtuel).

### Vérifier directement la table Gold dans AWS

Dans **Athena > Query editor**, sélectionner la région `eu-north-1`, le workgroup **`dnext-trade-wg`**, la source `AwsDataCatalog` et la base `dnext_trade`. Exécuter :

```sql
SELECT COUNT(*) AS total_flows,
       COUNT(DISTINCT anomaly_id) AS unique_ids,
       COUNT_IF(is_anomaly) AS anomalies
FROM dnext_trade.mirror_matches;
```

Résultat de référence pour le jeu initial : `142451`, `142451`, `27467`. Si `mirror_matches` ne figure pas dans l'éditeur, actualiser la liste des tables et vérifier la base/région ; ne pas chercher le bucket Gold dans la liste des tables.

---

## 8. Redéploiement dans un nouveau compte / environnement

> **Ne pas refaire ces opérations dans le compte partagé si le backend fonctionne déjà.** Ce guide est destiné à la reproduction ou à une reprise contrôlée. Il faut disposer des droits IAM, CloudFormation, Lambda, API Gateway, Glue, Athena et S3 nécessaires.

### Prérequis

1. Se connecter à **AWS CloudShell** (ou AWS CLI configurée) sur **`eu-north-1`**.
2. Vérifier l'identité : `aws sts get-caller-identity`.
3. Vérifier que la stack **`dnext-trade-data-platform`** (Bronze/Silver) existe et que la table `dnext_trade.trade_flows` est remplie. Le projet Silver est installé **séparément** ; ce dépôt ne le recrée pas.
4. Disposer d'un dossier de ce dépôt (`git clone` ou ZIP extrait), puis se placer à sa racine.

Vérification en lecture seule des ressources Silver :

```bash
aws cloudformation describe-stacks \
  --stack-name dnext-trade-data-platform --region eu-north-1 \
  --query 'Stacks[0].StackStatus' --output text

aws glue get-table --database-name dnext_trade --name trade_flows \
  --region eu-north-1 --query 'Table.Name' --output text
```

### Étape 1 — Créer le bucket Gold

```bash
aws cloudformation deploy \
  --stack-name dnext-trade-gold \
  --template-file infra/gold-storage.yaml \
  --region eu-north-1
```

### Étape 2 — Créer puis remplir la table Gold (une seule fois)

Dans Athena, sélectionner `dnext-trade-wg` et `dnext_trade` :

1. Ouvrir `sql/01_create_gold.sql`, remplacer `<ACCOUNT_ID>` par l'identifiant du compte AWS, lancer la requête et vérifier **`SUCCEEDED`**.
2. **Seulement si la table est vide**, ouvrir `sql/02_populate_gold.sql`, lancer une seule fois et vérifier **`SUCCEEDED`**.
3. Exécuter les **trois requêtes séparément** de `sql/03_validate_gold.sql`.

Le calcul s'exécute dans **Athena**, pas dans `deploy_api.sh` ; la table ne se remplit pas automatiquement lors du déploiement de l'API.

### Étape 3 — Déployer la Lambda et API Gateway

Dans un terminal à la racine du dépôt :

```bash
python3 -m unittest discover -s tests -v
bash scripts/deploy_api.sh
```

Le script utilise le bucket d'artefacts **`dnext-trade-artifacts-<account-id>`**, normalement créé par le déploiement de la Silver. Il envoie le package de code, crée / met à jour la stack `dnext-trade-backend`, puis appelle `aws lambda update-function-code` même si CloudFormation répond `No changes to deploy`.

L'URL de l'API est affichée à la fin. La récupérer aussi avec :

```bash
aws cloudformation describe-stacks --stack-name dnext-trade-backend \
  --region eu-north-1 --query 'Stacks[0].Outputs' --output table
```

**Dans un autre compte AWS, l'URL sera différente :** modifier l'URL utilisée par le frontend. Le compte et les noms de buckets référencés dans ce README sont ceux de la démonstration actuelle.

---

## 9. Maintenance, coûts et limites du MVP

### Mettre à jour le code de l'API

1. Modifier `src/app.py`.
2. Lancer les tests unitaires.
3. Exécuter `bash scripts/deploy_api.sh`.
4. Tester `/health` et les endpoints concernés.

**Le code de CloudShell n'est pas partagé automatiquement entre membres de l'équipe.** Conserver les sources dans le dépôt Git commun. Les ressources AWS, elles, sont visibles aux utilisateurs du **même compte** avec les permissions IAM appropriées.

### Reconstruire Gold après modification des données Silver

La Gold ne se met pas automatiquement à jour. Si la Silver change, organiser une reconstruction **contrôlée**, idéalement lorsque personne ne consulte l'API : sauvegarder / vérifier les données existantes, supprimer **uniquement** l'ancienne définition de table Gold et son préfixe S3 dédié, puis rejouer `01_create_gold.sql`, `02_populate_gold.sql` **une seule fois** et `03_validate_gold.sql`.

**Ne jamais vider le bucket Silver, le bucket Athena technique, ni tout le bucket Gold par erreur.** Ne pas relancer l'`INSERT` comme un simple « refresh ».

### Limites et choix actuels

- **API publique sans authentification**, suffisante pour une démonstration mais non pour un environnement de production. CORS ouvert (`*`) ; à restreindre au domaine Amplify ensuite.
- **Une seule Lambda** de consultation, timeout de 29 s, attente Athena de 20 s, concurrence réservée de 2, trafic API limité. Ces paramètres peuvent être revus si le nombre d'utilisateurs augmente.
- **Athena est appelé à la demande** pour les lectures. Le coût et la latence dépendent des requêtes ; le workgroup est configuré avec une limite de scan de 1 Go par requête.
- **Pas de pagination** des anomalies, maximum 100 résultats par appel.
- **Seuil fixé à 35 %**, non personnalisable dans l'API actuelle.
- **Pas de recalcul automatique** Gold, de cache applicatif ni d'endpoint de rapport IA dans ce dépôt.

### Si quelque chose ne fonctionne pas

| Symptôme | À vérifier en premier |
|---|---|
| La table Gold est invisible dans Athena | Région `eu-north-1`, base `dnext_trade`, actualisation des tables et droits Glue |
| La Gold est vide | Définition de table créée mais `02_populate_gold.sql` non exécuté / en échec |
| `GET /health` échoue | API Gateway, route, Lambda et logs CloudWatch |
| `/health` fonctionne mais pas `/heatmap` | Workgroup Athena, droits IAM, données Gold et logs Lambda |
| Erreur HTTP `400` | Formats ISO3, HS4 et dates (`YYYY-MM`) |
| La heatmap n'affiche rien | Filtres trop restrictifs ou absence de flux ; vérifier `/filters` |
| Risque de doublons Gold | Ne pas relancer `INSERT INTO` sans reconstruction préparée |

### Portée du projet

Ce dossier livre le **calcul Gold et l'API de consultation**. Le workflow de rapports IA (Step Functions, Bedrock, PDF, DynamoDB) est dans `reports/` et ajoute ses routes à cette API ; le frontend Vue.js reste à livrer (un frontend de démo est dans `frontend/`).
