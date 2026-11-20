#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rattrape dans jours.json les jours ou la collecte n'a rien retenu.

Les trois requetes de collecte.py sont relancees avec des bornes de dates
(after: / before:) au lieu de when:14d, par tranches de sept jours. Chaque
article inconnu de l'archive est range au jour ou la collecte quotidienne
l'aurait trouve : sa date de parution si c'est un jour ouvre, sinon le jour
ouvre suivant. Les articles qui tomberaient hors de la periode demandee sont
ignores.

Usage : python rattrapage.py AAAA-MM-JJ AAAA-MM-JJ
        (premier et dernier jour de collecte a rattraper, inclus)

N'ecrit que jours.json. Ne touche ni aux champs "summary" et "statut" ni aux
references deja presentes.
"""

import json
import re
import sys
import time
import urllib.error
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote
from xml.etree import ElementTree

import collecte as c

TRANCHE_JOURS = 7


def cle_titre(titre):
    return re.sub(r"[^a-z0-9]+", "", (titre or "").lower())[:90]


def jour_ouvre(d):
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def requete_bornee(requete, debut, fin):
    base = re.sub(r"\s*when:\S+", "", requete).strip()
    return "{} after:{} before:{}".format(base, debut.isoformat(), fin.isoformat())


def main():
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 1
    premier = date.fromisoformat(sys.argv[1])
    dernier = date.fromisoformat(sys.argv[2])
    maintenant = datetime.now(timezone.utc)

    # Un article paru un samedi ou un dimanche releve du lundi : on remonte
    # donc jusqu'a deux jours avant le premier jour demande.
    recherche_debut = premier - timedelta(days=3)
    recherche_fin = dernier + timedelta(days=1)

    trouves, rapport = [], []
    for cle, requete in c.REQUETES:
        d = recherche_debut
        while d < recherche_fin:
            f = min(d + timedelta(days=TRANCHE_JOURS), recherche_fin)
            q = requete_bornee(requete, d, f)
            try:
                items = c.parser_flux(c.recuperer_flux(c.BASE.format(quote(q, safe=""))), cle)
                etat = "ok"
            except (urllib.error.URLError, ElementTree.ParseError, OSError) as e:
                items, etat = [], "echec: {}".format(e)
            exclus = 0
            for it in items:
                if c.EXCLUSIONS.search(it["title"] + " " + it["source"]):
                    exclus += 1
                    continue
                trouves.append(it)
            rapport.append((cle, d.isoformat(), f.isoformat(), etat, len(items), exclus))
            print("{:15} {} -> {}  {}  bruts {:3}  exclus {}".format(
                cle, d, f, etat, len(items), exclus))
            d = f
            time.sleep(2)

    if rapport and all(r[3] != "ok" for r in rapport):
        print("toutes les requetes ont echoue : jours.json n'est pas modifie",
              file=sys.stderr)
        return 2

    archive = c.charger_archive()
    jours = {j["date"]: j for j in archive["jours"]
             if isinstance(j, dict) and j.get("date")}
    connus, titres_connus = set(), set()
    for j in jours.values():
        for it in j.get("items", []):
            connus.add(it.get("id") or c.identifiant(it.get("url", "")))
            connus.add(c.identifiant(it.get("gnews") or it.get("url", "")))
            titres_connus.add(cle_titre(it.get("title")))

    ajoutes, sans_date, hors_periode, deja = 0, 0, 0, 0
    par_jour = {}
    for it in sorted(trouves, key=lambda x: x["_ts"] or maintenant):
        ct = cle_titre(it["title"])
        if it["id"] in connus or (ct and ct in titres_connus):
            deja += 1
            continue
        if not it["_ts"]:
            sans_date += 1
            print("sans date, ignore : {} ({})".format(it["title"], it["source"]))
            continue
        cible = jour_ouvre(it["_ts"].astimezone(timezone.utc).date())
        if cible < premier or cible > dernier:
            hors_periode += 1
            continue

        url_finale, ok = c.resoudre(it["url"])
        j = jours.setdefault(cible.isoformat(), {"date": cible.isoformat(), "items": []})
        j.setdefault("items", []).append({
            "id": it["id"],
            "title": it["title"],
            "url": url_finale,
            "gnews": it["url"] if url_finale != it["url"] else "",
            "resolu": bool(ok),
            "source": it["source"],
            "published": it["published"],
            "feed": it["feed"],
            "summary": "",
            "statut": "",
            "rattrapage": maintenant.date().isoformat(),
        })
        j["rattrapage"] = maintenant.isoformat(timespec="seconds")
        connus.add(it["id"])
        if ct:
            titres_connus.add(ct)
        par_jour[cible.isoformat()] = par_jour.get(cible.isoformat(), 0) + 1
        ajoutes += 1
        print("+ {}  [{}] {} - {}".format(cible, it["published"], it["title"], it["source"]))

    plancher = (maintenant - timedelta(days=c.ARCHIVE_JOURS)).date().isoformat()
    liste = sorted((j for d, j in jours.items() if d >= plancher),
                   key=lambda j: j["date"], reverse=True)
    total = sum(len(j.get("items", [])) for j in liste)
    non_resolus = sum(1 for j in liste for it in j.get("items", []) if not it.get("resolu"))

    with open(c.ARCHIVE, "w", encoding="utf-8") as fh:
        json.dump({"maj": maintenant.isoformat(timespec="seconds"),
                   "jours_couverts": len(liste), "total": total,
                   "liens_non_resolus": non_resolus, "jours": liste},
                  fh, ensure_ascii=False, indent=1)

    print("rattrapage {} -> {} : {} reference(s) ajoutee(s), {} deja connue(s), "
          "{} hors periode, {} sans date".format(
              premier, dernier, ajoutes, deja, hors_periode, sans_date))
    for d in sorted(par_jour):
        print("  {} : {}".format(d, par_jour[d]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
