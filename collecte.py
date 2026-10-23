#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Collecte les flux Google Actualités sur les groupes d'entraide mutuelle.

Produit deux fichiers à la racine du dépôt :
  - veille.json : la collecte brute du jour (utile pour diagnostiquer)
  - jours.json  : l'archive jour par jour, que le site lit directement

jours.json est enrichi ensuite, éventuellement, par une tâche planifiée qui
remplit les champs "summary" et "statut". Ce script ne les écrase jamais.

Aucune dépendance : bibliothèque standard uniquement.
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from xml.etree import ElementTree

BASE = "https://news.google.com/rss/search?q={}&hl=fr&gl=FR&ceid=FR:fr"

# Les trois requêtes de veille. Modifiez-les ici, le reste suit.
REQUETES = [
    ("terme-exact",
     '"groupe d\'entraide mutuelle" OR "groupes d\'entraide mutuelle"'),
    ("sigle-qualifie",
     'GEM ("santé mentale" OR "handicap psychique" OR "pair-aidance") '
     '-"Grenoble École de Management" -Gémenos -festival'),
    ("federation",
     '"FIGEM" OR "CNIGEM"'),
]

FENETRE_JOURS = 10        # age maximal d'un element remonte par un flux
ARCHIVE_JOURS = 240       # profondeur conservee dans jours.json
MAX_PAR_FLUX = 40
UA = "veille-gem/2.0 (+github actions)"
ARCHIVE = "jours.json"
BRUT = "veille.json"

EXCLUSIONS = re.compile(
    r"gémenos|gemenos|grenoble école de management|grenoble ecole de management|"
    r"\bgem\s*(nature\s*run|festival)\b",
    re.IGNORECASE,
)


def identifiant(url):
    """Clé stable derivee de l'URL, pour retrouver un element d'un jour a l'autre."""
    h = 5381
    s = (url or "").strip().lower()
    s = re.sub(r"^https?://", "", s).rstrip("/")
    for c in s:
        h = ((h << 5) + h + ord(c)) & 0xFFFFFFFF
    return "u" + format(h, "x")


def recuperer(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def texte(noeud, defaut=""):
    return (noeud.text or defaut).strip() if noeud is not None else defaut


def parser_flux(xml_bytes, cle):
    racine = ElementTree.fromstring(xml_bytes)
    sortie = []
    for item in racine.iter("item"):
        titre_brut = texte(item.find("title"))
        lien = texte(item.find("link"))
        if not titre_brut or not lien:
            continue

        source = texte(item.find("source"))
        titre = titre_brut
        if source and titre_brut.endswith(" - " + source):
            titre = titre_brut[: -(len(source) + 3)].strip()
        elif " - " in titre_brut:
            titre, _, suffixe = titre_brut.rpartition(" - ")
            titre, source = titre.strip(), suffixe.strip()

        publie, horodatage = "", None
        brut = texte(item.find("pubDate"))
        if brut:
            try:
                horodatage = parsedate_to_datetime(brut)
                if horodatage.tzinfo is None:
                    horodatage = horodatage.replace(tzinfo=timezone.utc)
                publie = horodatage.date().isoformat()
            except (TypeError, ValueError):
                pass

        sortie.append({
            "id": identifiant(lien),
            "title": titre,
            "url": lien,
            "source": source,
            "published": publie,
            "feed": cle,
            "_ts": horodatage,
        })
    return sortie


def charger_archive():
    if not os.path.exists(ARCHIVE):
        return {"maj": None, "jours": []}
    try:
        with open(ARCHIVE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("jours"), list):
            return data
    except (ValueError, OSError) as e:
        print("archive illisible, on repart a zero : {}".format(e), file=sys.stderr)
    return {"maj": None, "jours": []}


def main():
    maintenant = datetime.now(timezone.utc)
    aujourdhui = maintenant.date().isoformat()
    limite = maintenant - timedelta(days=FENETRE_JOURS)

    # --- collecte ---
    collectes, rapport = [], []
    for cle, requete in REQUETES:
        url = BASE.format(quote(requete, safe=""))
        try:
            items = parser_flux(recuperer(url), cle)
            etat = "ok"
        except (urllib.error.URLError, ElementTree.ParseError, OSError) as e:
            items, etat = [], "echec: {}".format(e)
            print("flux {} : {}".format(cle, etat), file=sys.stderr)

        retenus = 0
        for it in items[:MAX_PAR_FLUX]:
            if it["_ts"] and it["_ts"] < limite:
                continue
            if EXCLUSIONS.search(it["title"] + " " + it["source"]):
                continue
            collectes.append(it)
            retenus += 1

        rapport.append({"cle": cle, "requete": requete, "etat": etat,
                        "bruts": len(items), "retenus": retenus})

    # --- dedoublonnage interne a la collecte ---
    vus_id, vus_titre, frais = set(), set(), []
    for it in sorted(collectes, key=lambda x: x["_ts"] or maintenant, reverse=True):
        cle_titre = re.sub(r"[^a-z0-9]+", "", it["title"].lower())[:90]
        if it["id"] in vus_id or (cle_titre and cle_titre in vus_titre):
            continue
        vus_id.add(it["id"])
        vus_titre.add(cle_titre)
        it.pop("_ts", None)
        frais.append(it)

    with open(BRUT, "w", encoding="utf-8") as f:
        json.dump({"genere": maintenant.isoformat(timespec="seconds"),
                   "fenetre_jours": FENETRE_JOURS, "flux": rapport,
                   "total": len(frais), "items": frais},
                  f, ensure_ascii=False, indent=1)

    # --- fusion dans l'archive ---
    archive = charger_archive()
    jours = {j["date"]: j for j in archive["jours"] if isinstance(j, dict) and j.get("date")}

    connus = set()
    titres_connus = set()
    for j in jours.values():
        for it in j.get("items", []):
            connus.add(it.get("id") or identifiant(it.get("url", "")))
            titres_connus.add(re.sub(r"[^a-z0-9]+", "", (it.get("title") or "").lower())[:90])

    jour = jours.setdefault(aujourdhui, {"date": aujourdhui, "items": []})
    jour.setdefault("items", [])
    deja_aujourdhui = {it.get("id") for it in jour["items"]}

    ajoutes = 0
    for it in frais:
        cle_titre = re.sub(r"[^a-z0-9]+", "", it["title"].lower())[:90]
        if it["id"] in connus or it["id"] in deja_aujourdhui:
            continue
        if cle_titre and cle_titre in titres_connus:
            continue
        jour["items"].append({
            "id": it["id"],
            "title": it["title"],
            "url": it["url"],
            "source": it["source"],
            "published": it["published"],
            "feed": it["feed"],
            "summary": "",     # rempli plus tard, jamais ecrase ici
            "statut": "",      # "", "retenu" ou "hors-sujet"
        })
        connus.add(it["id"])
        if cle_titre:
            titres_connus.add(cle_titre)
        ajoutes += 1

    jour["collecte"] = maintenant.isoformat(timespec="seconds")
    jour["flux"] = [{"cle": r["cle"], "etat": r["etat"], "retenus": r["retenus"]}
                    for r in rapport]

    # --- elagage et ecriture ---
    plancher = (maintenant - timedelta(days=ARCHIVE_JOURS)).date().isoformat()
    liste = [j for d, j in jours.items() if d >= plancher]
    liste.sort(key=lambda j: j["date"], reverse=True)

    total = sum(len(j.get("items", [])) for j in liste)
    archive = {"maj": maintenant.isoformat(timespec="seconds"),
               "jours_couverts": len(liste), "total": total, "jours": liste}

    with open(ARCHIVE, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=1)

    print("{} nouvelle(s) reference(s) ajoutee(s) au {} ; "
          "{} references sur {} jours dans l'archive"
          .format(ajoutes, aujourdhui, total, len(liste)))


if __name__ == "__main__":
    main()
