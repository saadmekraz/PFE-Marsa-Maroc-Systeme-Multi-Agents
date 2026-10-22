# ⚓ Marsa Maroc — Système Multi-Agents de Codification des Stocks

> Projet de Fin d'Études — Plateforme web IA pour la migration et la codification
> automatique des articles de stock (**export SAP/Apipro → SAP S/4HANA**).

![Aperçu de l'application](PFE-Marsa-Maroc-Systeme-Multi-Agents/docs/apercu.png)

## Fonctionnalités

- 📥 Import d'un export articles (`.xlsx` ou `.csv`)
- 🤖 Pipeline de **5 agents IA** exécuté en asynchrone, avec suivi en temps réel
- 📊 Tableau de bord : KPIs, répartition par famille, qualité des données
- 👤 File de validation *human-in-the-loop* pour les cas à faible confiance
- 🔁 Détection des doublons
- 📤 Export Excel (articles codifiés, validation expert, résultat complet)
- 🗄️ Sauvegarde MongoDB **optionnelle**
- 🔐 Authentification simple par session

## Architecture

```
┌──────────────────────────┐     HTTP/JSON      ┌─────────────────────────┐
│   FRONTEND (navigateur)  │  ───────────────►  │   BACKEND  (Flask API)  │
│  HTML · Tailwind · JS    │  ◄───────────────  │   main.py               │
│  Chart.js · agent rail   │                    │            │            │
└──────────────────────────┘                    │   PIPELINE 5 AGENTS     │
                                                │   marsa_pipeline.py     │
                                                │            │            │
                                                │   MongoDB (optionnel)   │
                                                │   marsa_mongo.py        │
                                                └─────────────────────────┘
```

## Les agents

| # | Agent | Rôle |
|---|-------|------|
| 1 | **Nettoyage** | Casse, séparation prestations / articles, score qualité |
| 2 | **Déduplication** | TF-IDF + plus proches voisins cosinus (scalable 50k+) |
| 3 | **Normalisation** | Convention SAP, description courte ≤ 40 caractères |
| 4 | **Classification** | Weak supervision (règles → classifieur), 22 familles, score de confiance |
| 5 | **Enrichissement** | Extraction marque / référence / dimensions depuis le texte libre |

Un **orchestrateur** (`OrchestrateurMarsa`) enchaîne les agents et produit le rapport final.

## Structure du projet

```
PFE-Marsa-Maroc-Systeme-Multi-Agents/
├── backend/
│   ├── main.py              # API Flask + service du frontend
│   ├── marsa_pipeline.py    # 5 agents + orchestrateur
│   └── marsa_mongo.py       # Persistance MongoDB (optionnelle)
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── data/
│   └── articles_synthetiques.csv   # ~5 300 articles de test
├── docs/apercu.png
└── requirements.txt
```

## Installation & lancement

**Prérequis :** Python 3.10+

```bash
git clone https://github.com/saadmekraz/PFE-Marsa-Maroc-Systeme-Multi-Agents.git
cd PFE-Marsa-Maroc-Systeme-Multi-Agents/PFE-Marsa-Maroc-Systeme-Multi-Agents
pip install -r requirements.txt
python backend/main.py
```

Ouvrir **http://localhost:5000**, se connecter, puis déposer un fichier
(par exemple `data/articles_synthetiques.csv`).

## Configuration

| Variable | Défaut | Rôle |
|----------|--------|------|
| `MARSA_USER` | `admin` | Identifiant de connexion |
| `MARSA_PASS` | `marsa2026` | Mot de passe |
| `MARSA_SECRET` | *(valeur de dev)* | Clé de session Flask |
| `MONGO_URI` | `mongodb://localhost:27017` | URI MongoDB |

> ⚠️ Changez les identifiants et la clé secrète avant tout déploiement.

## Endpoints API

| Méthode | Route | Rôle |
|---------|-------|------|
| POST | `/api/login` · `/api/logout` | Authentification |
| GET  | `/api/me` | Session courante |
| POST | `/api/upload` | Charge un fichier, retourne aperçu + `job_id` |
| POST | `/api/run/<job_id>` | Lance le pipeline (asynchrone) |
| GET  | `/api/status/<job_id>` | Avancement des agents |
| GET  | `/api/results/<job_id>/dashboard` | KPIs + données graphiques |
| GET  | `/api/results/<job_id>/validation` | File de validation humaine |
| GET  | `/api/results/<job_id>/duplicates` | Doublons détectés |
| GET  | `/api/results/<job_id>/codifies` | Articles codifiés |
| GET  | `/api/results/<job_id>/download/<kind>` | Export Excel (`codifies` · `validation` · `full`) |
| POST | `/api/mongo/test` | Teste la connexion MongoDB |
| POST | `/api/save/<job_id>` | Sauvegarde dans MongoDB |

## MongoDB (optionnel)

Base `marsa_maroc`, deux collections :

- `articles` — staging (tous les articles + sorties des agents)
- `articles_codifies` — destination finale

L'application fonctionne entièrement **sans** MongoDB.

## Stack technique

Python · Flask · pandas · NumPy · scikit-learn · openpyxl · PyMongo · Tailwind CSS · Chart.js
