# ═══════════════════════════════════════════════════════════════════════
# MARSA MAROC — Couche MongoDB
# Deux collections :
#   • articles            → staging (tous les articles + sorties des agents)
#   • articles_codifies   → DESTINATION FINALE (remplace l'ancien export SAP)
# MongoDB local par défaut : mongodb://localhost:27017
# ═══════════════════════════════════════════════════════════════════════

from datetime import datetime, timezone

URI_DEFAUT     = "mongodb://localhost:27017"
DB_DEFAUT      = "marsa_maroc"
COLL_STAGING   = "articles"
COLL_CODIFIES  = "articles_codifies"   # ← nouvelle collection (destination)


def _clean(v):
    s = str(v)
    return None if s in ("nan", "None", "", "NaT") else v


def doc_staging(row):
    return {
        "code_sap":   str(row.get("code_sap", "")),
        "type_texte": _clean(row.get("type_texte")),
        "date_ingestion": datetime.now(timezone.utc),
        "raw": {"description": _clean(row.get("description"))},
        "agent1": {"score": _clean(row.get("a1_score")),
                   "statut": _clean(row.get("a1_statut")),
                   "anomalies": _clean(row.get("a1_anomalies")),
                   "description_nettoyee": _clean(row.get("description_nettoyee"))},
        "agent2": {"cluster": _clean(row.get("a2_cluster")),
                   "decision": _clean(row.get("a2_doublon_decision")),
                   "alerte": _clean(row.get("a2_doublon_alerte")),
                   "est_maitre": bool(row.get("a2_est_maitre"))},
        "agent3": {"desc_courte": _clean(row.get("a3_desc_courte_sap")),
                   "desc_longue": _clean(row.get("a3_desc_longue_sap")),
                   "conforme": bool(row.get("a3_desc_conforme"))},
        "agent4": {"famille_n1": _clean(row.get("a4_famille_n1")),
                   "confiance": float(row.get("a4_confiance") or 0),
                   "source": _clean(row.get("a4_source")),
                   "validation_requise": bool(row.get("a4_validation_requise"))},
        "agent5": {"marque": _clean(row.get("a5_marque")),
                   "reference_fab": _clean(row.get("a5_reference_fab")),
                   "dimensions": _clean(row.get("a5_dimensions")),
                   "specification": _clean(row.get("a5_specification")),
                   "attributs": _clean(row.get("a5_attributs"))},
        "statut_final": _clean(row.get("statut_final")) or "EN_ATTENTE",
    }


def doc_codifie(row):
    return {
        "code_article":   str(row.get("code_sap", "")),
        "description":    _clean(row.get("a3_desc_courte_sap")),
        "description_longue": _clean(row.get("a3_desc_longue_sap")),
        "famille":        _clean(row.get("a4_famille_n1")),
        "confiance_classification": float(row.get("a4_confiance") or 0),
        "marque":         _clean(row.get("a5_marque")),
        "reference_fab":  _clean(row.get("a5_reference_fab")),
        "dimensions":     _clean(row.get("a5_dimensions")),
        "statut":         "CODIFIÉ",
        "date_codification": datetime.now(timezone.utc),
    }


def tester_connexion(uri=URI_DEFAUT):
    try:
        from pymongo import MongoClient
        client = MongoClient(uri or URI_DEFAUT, serverSelectionTimeoutMS=4000)
        client.admin.command("ping")
        return True, "Connexion MongoDB réussie"
    except Exception as e:
        return False, f"Échec connexion : {e}"


def sauvegarder_staging(df, uri=URI_DEFAUT, db=DB_DEFAUT, coll=COLL_STAGING):
    from pymongo import MongoClient, UpdateOne
    c = MongoClient(uri or URI_DEFAUT, serverSelectionTimeoutMS=5000)[db][coll]
    c.create_index([("code_sap", 1)], unique=False)
    ops = [UpdateOne({"code_sap": str(r.get("code_sap", ""))},
                     {"$set": doc_staging(r)}, upsert=True)
           for _, r in df.iterrows()]
    ins = mod = 0
    for i in range(0, len(ops), 1000):
        res = c.bulk_write(ops[i:i + 1000], ordered=False)
        ins += res.upserted_count; mod += res.modified_count
    return ins, mod, c.count_documents({})


def sauvegarder_codifies(df, uri=URI_DEFAUT, db=DB_DEFAUT, coll=COLL_CODIFIES):
    """N'enregistre que les articles au statut CODIFIÉ dans la collection
    destination articles_codifies (remplace l'export SAP)."""
    from pymongo import MongoClient, UpdateOne
    c = MongoClient(uri or URI_DEFAUT, serverSelectionTimeoutMS=5000)[db][coll]
    c.create_index([("code_article", 1)], unique=True)
    codifies = df[df["statut_final"] == "CODIFIÉ"]
    ops = [UpdateOne({"code_article": str(r.get("code_sap", ""))},
                     {"$set": doc_codifie(r)}, upsert=True)
           for _, r in codifies.iterrows()]
    ins = mod = 0
    for i in range(0, len(ops), 1000):
        res = c.bulk_write(ops[i:i + 1000], ordered=False)
        ins += res.upserted_count; mod += res.modified_count
    return ins, mod, c.count_documents({})
