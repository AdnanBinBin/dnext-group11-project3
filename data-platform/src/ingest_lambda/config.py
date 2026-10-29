"""Référentiels et règles de qualité de la couche Silver.

Tout ce qui est "métier" (périmètre, codes, seuils) est centralisé ici pour
pouvoir être justifié dans le rapport et modifié sans toucher au code.
"""

# Colonnes attendues dans le fichier source UN Comtrade (Bilateral_data.xlsx)
REQUIRED_COLUMNS = [
    "YearMonth", "Reporter", "Flow", "Partner",
    "HS4", "Product", "Value_USD", "Qty_t",
]

# Périmètre du dataset (cf. Bilateral_data_description.pdf)
SCOPE_EXPORTERS = {"USA", "BRA", "ARG", "CAN", "AUS", "RUS", "UKR", "FRA", "KAZ", "ROU"}

# Référentiel produits HS4 -> libellé officiel
HS4_PRODUCTS = {
    "1001": "Wheat",
    "1003": "Barley",
    "1005": "Corn",
    "1201": "Soybeans",
    "1205": "Rapeseed",
    "1206": "Sunflower seed",
    "2304": "Soybean meal",
}

VALID_FLOWS = {"EXPORT": "Export", "IMPORT": "Import"}

# Libellés partenaires non ISO3 trouvés dans l'extraction Comtrade.
# Ce sont des pays réels avec un libellé abrégé : on les ramène à leur code
# ISO3 (normalisation des codes pays).
PARTNER_CODE_MAPPING = {
    "ALBA": "ALB",          # Albania
    "CURAC": "CUW",         # Curaçao
    "NORTH_MACED": "MKD",   # North Macedonia
}

# Zones agrégées "nes" (not elsewhere specified) de Comtrade : ce ne sont pas
# des pays, il n'existe donc pas de déclaration miroir. On les garde (ce sont
# des transactions légitimes) mais on les marque.
AGGREGATE_PARTNERS = {
    "OTHER_ASIA",   # "Other Asia, nes" (Comtrade S19)
    "EUR_OTHERS",   # "Europe, nes"
    "_X_",          # "Areas, nes" — destination non spécifiée
}

# Premier mois couvert par le dataset
MIN_YEAR = 2019

# Tonnage en dessous duquel une déclaration est marquée LOW_QTY (flag qualité
# informatif, ex. 11 USD pour 0.021 t). La ligne est conservée telle quelle.
LOW_QTY_THRESHOLD_T = 1.0
