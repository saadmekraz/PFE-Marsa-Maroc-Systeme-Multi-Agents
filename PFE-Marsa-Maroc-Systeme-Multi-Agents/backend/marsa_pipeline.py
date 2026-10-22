# ═══════════════════════════════════════════════════════════════════════
# MARSA MAROC — PIPELINE IA AGENTIQUE (adapté à l'export SAP réel)
# ───────────────────────────────────────────────────────────────────────
# Données réelles : 2 champs utiles → code matière SAP + description.
# Agent 1 Nettoyage · Agent 2 Déduplication · Agent 3 Normalisation
# Agent 4 Classification (weak-supervision) · Agent 5 Enrichissement
# Agent 6 Optimisation stock → hors périmètre (pas de données stock/prix)
# ═══════════════════════════════════════════════════════════════════════

import re
import unicodedata
import numpy as np
import pandas as pd
from datetime import datetime
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score

# ── Mapping des colonnes de l'export SAP réel vers le schéma interne
COLONNES_SAP = {
    "Nom de texte":    "code_sap",     # code matière SAP (clé)
    "Texte descript.": "description",  # description article
    "ID":              "type_texte",   # GRUN / BEST / PRUE
}

SEUIL_DOUBLON_AUTO   = 0.90   # ≥ → fusion automatique
SEUIL_DOUBLON_ALERTE = 0.85   # ≥ → alerte / validation humaine
SEUIL_CONF_CLASSIF   = 0.45   # < → VALIDATION_HUMAINE (Agent 4) — calibré pour ≥70% auto


def charger_export_sap(chemin, sheet=0):
    """Lit un export SAP/Apipro (.xlsx OU .csv) et le remappe vers le schéma
    interne. Détecte automatiquement le format et, pour le CSV, le séparateur."""
    df = None

    def _depuis_csv(source):
        # auto-détection du séparateur (, ; ou tab) via le moteur python
        return pd.read_csv(source, dtype=str, sep=None, engine="python",
                           encoding_errors="replace")

    # cas 1 : chemin texte → on se fie à l'extension
    if isinstance(chemin, str):
        if chemin.lower().endswith((".csv", ".txt")):
            df = _depuis_csv(chemin)
        else:
            df = pd.read_excel(chemin, sheet_name=sheet, dtype=str)
    else:
        # cas 2 : flux en mémoire (BytesIO) → on renifle les 1ers octets.
        # un .xlsx est une archive ZIP : signature "PK\x03\x04".
        pos = chemin.tell() if hasattr(chemin, "tell") else 0
        head = chemin.read(4)
        chemin.seek(pos)
        if head[:2] == b"PK":
            df = pd.read_excel(chemin, sheet_name=sheet, dtype=str)
        else:
            df = _depuis_csv(chemin)

    df = df.rename(columns=COLONNES_SAP)
    for c in ["code_sap", "description", "type_texte"]:
        if c not in df.columns:
            df[c] = ""
    df["description"] = df["description"].fillna("").astype(str)
    df["code_sap"]    = df["code_sap"].fillna("").astype(str)
    return df[["code_sap", "description", "type_texte"]].copy()


def _strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


# ═══════════════════════════════════════════════════════════════════════
# AGENT 1 — NETTOYAGE & PROFILING
# ═══════════════════════════════════════════════════════════════════════
class Agent1_Nettoyage:
    """Détecte casse, doublons de saisie, descriptions vides/courtes,
    références embarquées et articles qui sont en fait des prestations."""

    RE_SERVICE = re.compile(
        r"CONTROLE\s+REGLEMENTAIRE|MAIN\s*D.?\s*OEUVRE|MECANICIEN|FICHE\s+D.?ABSENCE|"
        r"TRAITEMENT\s+DE\s+SURFACE|PRESTATION|REPARATION|LOCATION|FORMATION|"
        r"INSTRUCTION\s+DE\s+SERVICE|CONTROLE\s+TECHNIQUE",
        re.IGNORECASE)
    RE_REF = re.compile(r"\bREF\s*[:.]", re.IGNORECASE)

    def _normaliser_casse(self, texte):
        if not isinstance(texte, str) or not texte.strip():
            return texte
        mots = texte.split()
        out = []
        for m in mots:
            # garde les sigles/refs alphanum (HP, RJ45, AW46, 6ES7…) tels quels
            if re.match(r"^[A-Z0-9][A-Z0-9\-/.:]{1,}$", m) and any(ch.isdigit() for ch in m):
                out.append(m)
            elif re.match(r"^[A-Z]{2,5}$", m):           # sigle court : HP, FTP, EP
                out.append(m)
            else:
                out.append(m.capitalize())
        return " ".join(out)

    def analyser(self, df):
        df = df.copy()
        scores, statuts, anomalies, descs_propres = [], [], [], []
        for _, row in df.iterrows():
            desc = str(row.get("description", "")).strip()
            anos, score = [], 100

            if not desc or desc.lower() == "nan":
                anos.append("DESC_VIDE"); score = 0
            elif len(desc) < 4:
                anos.append("DESC_TROP_COURTE"); score -= 40

            if desc and desc == desc.upper() and any(c.isalpha() for c in desc):
                anos.append("CASSE:tout_majuscule"); score -= 5
            elif desc and desc == desc.lower() and len(desc) > 5:
                anos.append("CASSE:tout_minuscule"); score -= 5

            if self.RE_SERVICE.search(desc):
                anos.append("PRESTATION_NON_ARTICLE"); score -= 15
            if self.RE_REF.search(desc):
                anos.append("REF_EMBARQUEE")  # info utile, pas une pénalité

            score = max(0, score)
            scores.append(score)
            anomalies.append("; ".join(anos))
            descs_propres.append(self._normaliser_casse(desc))

            if score == 0:
                statuts.append("CORROMPU")
            elif "PRESTATION_NON_ARTICLE" in anos:
                statuts.append("PRESTATION")
            elif score >= 95:
                statuts.append("VALIDE")
            else:
                statuts.append("ALERTE")

        df["a1_score"]              = scores
        df["a1_statut"]             = statuts
        df["a1_anomalies"]          = anomalies
        df["description_nettoyee"]  = descs_propres
        return df


# ═══════════════════════════════════════════════════════════════════════
# AGENT 2 — DÉDUPLICATION (scalable, top-k nearest neighbors)
# ═══════════════════════════════════════════════════════════════════════
class Agent2_Deduplication:
    """Détecte les doublons sémantiques sur 50k+ articles SANS matrice
    dense O(n²) : TF-IDF char n-grams + NearestNeighbors cosine top-k."""

    def __init__(self, k=6):
        self.k = k
        self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4),
                                          max_features=20000)

    def analyser(self, df):
        df = df.copy().reset_index(drop=True)
        descriptions = df["description_nettoyee"].fillna("").tolist()
        X = self.vectorizer.fit_transform(descriptions)

        k = min(self.k, len(df))
        nn = NearestNeighbors(n_neighbors=k, metric="cosine", algorithm="brute")
        nn.fit(X)
        distances, indices = nn.kneighbors(X)

        alertes   = [""] * len(df)
        decisions = ["UNIQUE"] * len(df)
        groupe_id = list(range(len(df)))   # union-find léger pour clusters
        paires = []

        def find(a):
            while groupe_id[a] != a:
                groupe_id[a] = groupe_id[groupe_id[a]]
                a = groupe_id[a]
            return a

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                groupe_id[max(ra, rb)] = min(ra, rb)

        for i in range(len(df)):
            for dist, j in zip(distances[i], indices[i]):
                if j == i:
                    continue
                sim = 1.0 - dist
                if sim >= SEUIL_DOUBLON_ALERTE:
                    decision = "AUTO_FUSION" if sim >= SEUIL_DOUBLON_AUTO else "VALIDATION_HUMAINE"
                    if not alertes[i] or sim > float(alertes[i].split("(")[1].split(")")[0]):
                        alertes[i]   = f"DOUBLON({sim:.2f}):{df.iloc[j]['code_sap']}->{decision}"
                        decisions[i] = decision
                    if i < j:
                        paires.append((i, j, round(sim, 3)))
                        if sim >= SEUIL_DOUBLON_AUTO:
                            union(i, j)

        df["a2_cluster"]          = [find(i) for i in range(len(df))]
        df["a2_doublon_alerte"]   = alertes
        df["a2_doublon_decision"] = decisions
        # article "maître" du cluster = 1er code par cluster
        maitres = df.groupby("a2_cluster")["code_sap"].transform("first")
        df["a2_est_maitre"]       = df["code_sap"] == maitres
        return df, paires


# ═══════════════════════════════════════════════════════════════════════
# AGENT 3 — NORMALISATION (convention SAP, ≤ 40 caractères)
# ═══════════════════════════════════════════════════════════════════════
class Agent3_Normalisation:
    ABREVIATIONS = {
        "electrique": "ELEC", "électrique": "ELEC", "hydraulique": "HYDR",
        "pneumatique": "PNEU", "industrielle": "IND", "industriel": "IND",
        "reference": "REF", "référence": "REF", "diametre": "DIA",
        "diamètre": "DIA", "longueur": "LG", "marque": "MQ",
        "equivalent": "EQUIV", "équivalent": "EQUIV", "complet": "CPLT",
        "roulement": "ROUL", "fourniture": "FOURN",
    }

    def _abreger(self, desc):
        out = desc
        for mot, ab in self.ABREVIATIONS.items():
            out = re.sub(rf"\b{mot}\b", ab, out, flags=re.IGNORECASE)
        return out

    def _desc_courte(self, desc, max_len=40):
        if not isinstance(desc, str):
            return ""
        d = re.sub(r"\s+", " ", desc).strip()
        if len(d) <= max_len:
            return d.upper()
        d = self._abreger(d)
        d = re.sub(r"\s+", " ", d).strip()
        if len(d) <= max_len:
            return d.upper()
        return d[:max_len].rsplit(" ", 1)[0].upper()

    def analyser(self, df):
        df = df.copy()
        src = df["description_nettoyee"].fillna(df["description"])
        df["a3_desc_courte_sap"] = src.apply(self._desc_courte)
        df["a3_desc_longue_sap"] = src.apply(
            lambda d: re.sub(r"\s+", " ", str(d)).strip().upper())
        df["a3_desc_conforme"]   = df["a3_desc_courte_sap"].str.len() <= 40
        return df


# ═══════════════════════════════════════════════════════════════════════
# AGENT 4 — CLASSIFICATION (weak supervision : règles → classifieur)
# ═══════════════════════════════════════════════════════════════════════
class Agent4_Classification:
    """1) règles mots-clés étiquettent les cas confiants (~50%)
       2) un classifieur TF-IDF apprend de ces étiquettes
       3) il généralise au reste, avec score de confiance → human-in-the-loop"""

    TAXONOMIE = {
        "Roulements & Paliers":          r"ROULEMENT|PALIER|BUTEE|\bBAGUE\b",
        "Joints & Étanchéité":           r"JOINT|ETANCH|\bSPI\b|GARNITURE|CAOUTCHOU",
        "Filtres":                       r"FILTRE|CARTOUCHE FILTR",
        "Lubrifiants & Huiles":          r"HUILE|GRAISSE|GREASE|LUBRIF|\bOIL\b|\bOGL\b|GRAISS",
        "Câbles & Connectique":          r"\bCABLE\b|COSSE|CONNECT|\bRJ\s?45\b|\bFTP\b|FICHE RJ|BORNIER",
        "Appareillage Électrique":       r"DISJONCTEUR|CONTACTEUR|RELAIS|FUSIBLE|SECTIONNEUR|\bBORNE\b|TRANSFORMATEUR",
        "Capteurs & Mesure":             r"CAPTEUR|SONDE|DETECTEUR|FIN DE COURSE|AFFICHEUR|\bJAUGE\b|MANOMETRE",
        "Cartes & Modules Électroniques":r"\bCARTE\b|\bMODULE\b|AUTOMATE|PROCESSEUR|ONDULEUR|VARIATEUR|ALIMENTATION",
        "Éclairage":                     r"\bLAMPE\b|PROJECTEUR|TUBE FLUO|\bLED\b|AMPOULE|DOUILLE",
        "Pompes":                        r"\bPOMPE\b",
        "Moteurs & Alternateurs":        r"\bMOTEUR\b|ALTERNATEUR|\bMOTO",
        "Vérins & Hydraulique":          r"\bVERIN\b|FLEXIBLE|DISTRIBUTEUR HYDR|VALVE|\bDURIT",
        "Visserie & Fixation":           r"\bVIS\b|BOULON|\bECROU\b|RONDELLE|GOUJON|TIGE FILET",
        "Transmission Mécanique":        r"COURROIE|\bCHAINE\b|PIGNON|ENGRENAGE|POULIE|CARDAN",
        "Élingues & Levage":             r"ELINGUE|MANILLE|CROCHET|\bSANGLE\b|PALAN",
        "Tuyauterie & Flexibles":        r"\bTUBE\b|TUYAU|RACCORD|\bTE\b|\bCOUDE\b",
        "Outillage":                     r"PERCEUSE|MEULEUSE|\bCLE\b|DOUILLE IMPACT|MARTEAU|TOURNEVIS|\bOUTIL",
        "EPI & Sécurité":                r"CASQUE|\bGANT\b|CHAUSSURE|\bBOTTE\b|HARNAIS|LUNETTE|EXTINCTEUR|LANCE INCENDIE",
        "Consommables Bureautiques":     r"TONER|CARTOUCHE ENCRE|\bPAPIER\b|STYLO|CHEMISE",
        "Batteries & Énergie":           r"BATTERIE|ACCUMULATEUR|\bPILE\b",
        "Prestations & Services":        r"CONTROLE REGLEMENTAIRE|PRESTATION|MAIN.?D.?OEUVRE|MECANICIEN|TRAITEMENT DE SURFACE|REPARATION",
    }
    AUTRES = "Autres / À classifier"

    def __init__(self):
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), max_features=8000)
        self.clf = LogisticRegression(max_iter=1000, C=4.0, class_weight="balanced")
        self.entraine = False
        self.precision_test = None

    def _label_par_regles(self, desc):
        d = _strip_accents(str(desc)).upper()
        for famille, pat in self.TAXONOMIE.items():
            if re.search(_strip_accents(pat).upper(), d):
                return famille
        return None

    def entrainer(self, df):
        labels = df["description_nettoyee"].apply(self._label_par_regles)
        mask = labels.notna()
        couverture = mask.mean()
        Xtxt = df.loc[mask, "description_nettoyee"].fillna("")
        y    = labels[mask]
        strat = y if y.value_counts().min() >= 2 else None
        Xtr, Xte, ytr, yte = train_test_split(Xtxt, y, test_size=0.2,
                                              random_state=42, stratify=strat)
        Xtr_v = self.vec.fit_transform(Xtr)
        self.clf.fit(Xtr_v, ytr)
        self.precision_test = accuracy_score(yte, self.clf.predict(self.vec.transform(Xte)))
        self.entraine = True
        print(f"  [Agent 4] Étiquetage règles : {couverture*100:.0f}% du catalogue "
              f"({mask.sum()} articles) | précision test : {self.precision_test*100:.1f}%")
        return self.precision_test

    def analyser(self, df):
        if not self.entraine:
            raise RuntimeError("Agent 4 : appeler .entrainer() avant .analyser()")
        df = df.copy()
        X = self.vec.transform(df["description_nettoyee"].fillna(""))
        proba = self.clf.predict_proba(X)
        pred  = self.clf.classes_[proba.argmax(axis=1)]
        conf  = proba.max(axis=1)
        # étiquette-règle prioritaire si elle existe (haute fiabilité)
        regle = df["description_nettoyee"].apply(self._label_par_regles)
        famille_finale = [r if pd.notna(r) else p for r, p in zip(regle, pred)]
        conf_finale    = [1.0 if pd.notna(r) else c for r, c in zip(regle, conf)]
        df["a4_famille_n1"]         = famille_finale
        df["a4_confiance"]          = np.round(conf_finale, 3)
        df["a4_source"]             = ["regle" if pd.notna(r) else "classifieur" for r in regle]
        df["a4_validation_requise"] = df["a4_confiance"] < SEUIL_CONF_CLASSIF
        df.loc[df["a4_validation_requise"], "a4_famille_n1"] = self.AUTRES
        return df


# ═══════════════════════════════════════════════════════════════════════
# AGENT 5 — ENRICHISSEMENT (extraction d'attributs depuis la description)
# ═══════════════════════════════════════════════════════════════════════
class Agent5_Enrichissement:
    """Extrait marque, référence fabricant, dimensions et unité présumée
    directement depuis le texte libre — pas de complétion par médiane
    (aucune donnée prix/stock dans l'export)."""

    MARQUES = ["SKF", "SIEMENS", "BOSCH", "ABB", "SCHNEIDER", "PHILIPS", "FAG",
               "NSK", "SHELL", "CASTROL", "TOTAL", "HP", "SCHMERSAL", "MENNEKES",
               "SCAME", "KONECRANES", "TERBERG", "STAUFF", "TAKRAF", "LEGRAND"]
    RE_REF  = re.compile(r"REF\s*[:.]?\s*([A-Z0-9][A-Z0-9\-/.]{2,})", re.IGNORECASE)
    RE_DIM  = re.compile(r"\b(\d+(?:[.,]\d+)?\s*[xX]\s*\d+(?:[.,]\d+)?(?:\s*[xX]\s*\d+(?:[.,]\d+)?)?)\b")
    RE_DN   = re.compile(r"\b(DN\s?\d+|D\s?\d+\s?MM|\d+\s?MM|\d+\s?V\b|\d+\s?A\b|\d+\s?KVA)\b", re.IGNORECASE)

    def _marque(self, d):
        u = d.upper()
        for m in self.MARQUES:
            if re.search(rf"\b{m}\b", u):
                return m
        return ""

    def analyser(self, df):
        df = df.copy()
        marques, refs, dims, specs, actions = [], [], [], [], []
        for _, row in df.iterrows():
            d = str(row.get("description", ""))
            mq = self._marque(d)
            rf = self.RE_REF.search(d)
            dm = self.RE_DIM.search(d)
            sp = self.RE_DN.search(d)
            rf = rf.group(1) if rf else ""
            dm = dm.group(1) if dm else ""
            sp = sp.group(1) if sp else ""
            found = [x for x in [("marque", mq), ("ref", rf), ("dim", dm), ("spec", sp)] if x[1]]
            marques.append(mq); refs.append(rf); dims.append(dm); specs.append(sp)
            actions.append("; ".join(f"{k}:{v}" for k, v in found) if found else "aucun_attribut")
        df["a5_marque"]            = marques
        df["a5_reference_fab"]     = refs
        df["a5_dimensions"]        = dims
        df["a5_specification"]     = specs
        df["a5_attributs"]         = actions
        df["a5_validation_requise"] = (df["a5_attributs"] == "aucun_attribut") & \
                                       (df["description"].str.len() > 120)
        return df


# ═══════════════════════════════════════════════════════════════════════
# ORCHESTRATEUR
# ═══════════════════════════════════════════════════════════════════════
class OrchestrateurMarsa:
    def __init__(self, verbose=True):
        self.verbose = verbose
        self.a1 = Agent1_Nettoyage()
        self.a2 = Agent2_Deduplication()
        self.a3 = Agent3_Normalisation()
        self.a4 = Agent4_Classification()
        self.a5 = Agent5_Enrichissement()
        self.rapport = {}

    def _log(self, *a):
        if self.verbose:
            print(*a)

    def executer(self, df_brut):
        self._log("\n" + "═" * 60)
        self._log("  MARSA MAROC — Pipeline IA Agentique (5 agents actifs)")
        self._log("═" * 60)
        self._log(f"  Articles en entrée : {len(df_brut)}")

        self._log("\n  [Agent 1] Nettoyage & profiling…")
        df = self.a1.analyser(df_brut)
        vc = df["a1_statut"].value_counts().to_dict()
        self._log(f"    → {vc}")

        self._log("\n  [Agent 4] Entraînement classifieur (weak supervision)…")
        self.a4.entrainer(df[df["a1_statut"] != "CORROMPU"])

        self._log("\n  [Agent 2] Déduplication (top-k cosine)…")
        df_propre = df[df["a1_statut"] != "CORROMPU"].copy()
        df_propre, paires = self.a2.analyser(df_propre)
        n_auto  = (df_propre["a2_doublon_decision"] == "AUTO_FUSION").sum()
        n_val   = (df_propre["a2_doublon_decision"] == "VALIDATION_HUMAINE").sum()
        n_clust = df_propre["a2_cluster"].nunique()
        self._log(f"    → Paires≥0.85:{len(paires)} | Auto-fusion:{n_auto} | "
                  f"À valider:{n_val} | Clusters uniques:{n_clust}")
        df = df.merge(
            df_propre[["code_sap", "a2_cluster", "a2_doublon_alerte",
                       "a2_doublon_decision", "a2_est_maitre"]].drop_duplicates("code_sap"),
            on="code_sap", how="left")
        df["a2_doublon_decision"] = df["a2_doublon_decision"].fillna("CORROMPU_EXCLU")
        df["a2_doublon_alerte"]   = df["a2_doublon_alerte"].fillna("")
        df["a2_est_maitre"]       = df["a2_est_maitre"].fillna(False)

        self._log("\n  [Agent 3] Normalisation SAP (≤40 car.)…")
        df = self.a3.analyser(df)
        self._log(f"    → Conformes : {df['a3_desc_conforme'].sum()}/{len(df)}")

        self._log("\n  [Agent 4] Classification taxonomique…")
        df = self.a4.analyser(df)
        self._log(f"    → Familles : {df['a4_famille_n1'].nunique()} | "
                  f"Validation requise : {df['a4_validation_requise'].sum()}")

        self._log("\n  [Agent 5] Enrichissement (extraction attributs)…")
        df = self.a5.analyser(df)
        n_attr = (df["a5_attributs"] != "aucun_attribut").sum()
        self._log(f"    → Articles avec attributs extraits : {n_attr}")

        df["statut_final"] = df.apply(self._statut_final, axis=1)
        self._construire_rapport(df)
        return df

    def _statut_final(self, row):
        if row.get("a1_statut") == "CORROMPU":
            return "BLOQUÉ"
        if row.get("a4_validation_requise") or row.get("a5_validation_requise"):
            return "VALIDATION_HUMAINE"
        if row.get("a2_doublon_decision") == "VALIDATION_HUMAINE":
            return "VALIDATION_HUMAINE"
        if row.get("a2_doublon_decision") == "AUTO_FUSION" and not row.get("a2_est_maitre"):
            return "DOUBLON_FUSIONNÉ"
        return "CODIFIÉ"

    def _construire_rapport(self, df):
        sf = df["statut_final"].value_counts().to_dict()
        total = len(df)
        codifies = sf.get("CODIFIÉ", 0)
        self.rapport = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "total": total,
            "codifies": codifies,
            "doublons_fusionnes": sf.get("DOUBLON_FUSIONNÉ", 0),
            "validation_humaine": sf.get("VALIDATION_HUMAINE", 0),
            "bloques": sf.get("BLOQUÉ", 0),
            "taux_auto": round((codifies + sf.get("DOUBLON_FUSIONNÉ", 0)) / total * 100, 1),
            "score_qualite_moyen": round(df["a1_score"].mean(), 1),
            "familles": df["a4_famille_n1"].value_counts().to_dict(),
            "precision_classif": round((self.a4.precision_test or 0) * 100, 1),
        }

    def afficher_rapport(self):
        r = self.rapport
        print("\n" + "═" * 60)
        print("  RAPPORT FINAL")
        print("═" * 60)
        print(f"  Total articles            : {r['total']}")
        print(f"  Taux traitement auto      : {r['taux_auto']}%")
        print(f"  Score qualité moyen       : {r['score_qualite_moyen']}/100")
        print(f"  Précision classification  : {r['precision_classif']}%")
        print(f"  ✓ Codifiés                : {r['codifies']}")
        print(f"  ⧉ Doublons fusionnés      : {r['doublons_fusionnes']}")
        print(f"  ⚠ Validation humaine      : {r['validation_humaine']}")
        print(f"  ✗ Bloqués                 : {r['bloques']}")
        print("═" * 60)


if __name__ == "__main__":
    import sys
    chemin = sys.argv[1] if len(sys.argv) > 1 else "EXPORT_20260511080354_ARTICLE_SAP.xlsx"
    brut = charger_export_sap(chemin)
    orch = OrchestrateurMarsa()
    res = orch.executer(brut)
    orch.afficher_rapport()
