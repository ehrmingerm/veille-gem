#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Collecte les flux Google Actualités sur les groupes d'entraide mutuelle.

Produit deux fichiers à la racine du dépôt :
  - veille.json : la collecte brute du jour (utile pour diagnostiquer)
  - jours.json  : l'archive jour par jour, que le site lit directement

Les liens rendus par Google Actualités passent par une redirection qui masque
l'adresse réelle de l'article. Le script tente de la résoudre, par deux voies
successives, et conserve le lien d'origine en cas d'échec.

jours.json est enrichi ensuite, éventuellement, par une tâche planifiée qui
remplit les champs "summary" et "statut". Ce script ne les écrase jamais.

Aucune dépendance : bibliothèque standard uniquement.
"""

import base64
import binascii
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlparse
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
RETENTATIVES_RESOLUTION = 12   # anciens liens non resolus retentes a chaque passage

UA_FLUX = "veille-gem/3.0 (+github actions)"
UA_PAGE = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")

ARCHIVE = "jours.json"
BRUT = "veille.json"

EXCLUSIONS = re.compile(
    r"gémenos|gemenos|grenoble école de management|grenoble ecole de management|"
    r"\bgem\s*(nature\s*run|festival)\b",
    re.IGNORECASE,
)

# Hotes qui ne sont jamais un article : plateformes, mesure d'audience, CDN,
# regies, reseaux sociaux. Une adresse de ce type signale une erreur de lecture.
HOTES_A_IGNORER = re.compile(
    r"(^|\.)("
    r"google\.[a-z.]{2,}|google-analytics\.com|googletagmanager\.com|"
    r"googleapis\.com|googlesyndication\.com|googleadservices\.com|"
    r"googleusercontent\.com|gstatic\.com|ggpht\.com|doubleclick\.net|"
    r"ampproject\.org|youtube\.com|youtu\.be|blogger\.com|"
    r"schema\.org|w3\.org|example\.com|localhost|"
    r"facebook\.com|twitter\.com|x\.com|linkedin\.com|instagram\.com|"
    r"pinterest\.[a-z.]{2,}|whatsapp\.com|t\.me|reddit\.com|tiktok\.com|"
    r"cloudflare\.com|jsdelivr\.net|unpkg\.com|bootstrapcdn\.com|jquery\.com|"
    r"cookielaw\.org|onetrust\.com|didomi\.io|sentry\.io|hotjar\.com|"
    r"chartbeat\.com|outbrain\.com|taboola\.com|criteo\.com|adsrvr\.org|"
    r"amazon-adsystem\.com|adnxs\.com|scorecardresearch\.com|quantserve\.com"
    r")$", re.IGNORECASE)

# Extensions de fichier qui ne sont jamais une page d'article.
EXTENSIONS_A_IGNORER = re.compile(
    r"\.(js|mjs|css|map|json|xml|txt|ico|png|jpe?g|gif|svg|webp|avif|bmp|"
    r"woff2?|ttf|otf|eot|mp4|webm|mp3|wav|zip|gz)$", re.IGNORECASE)

# Chemins techniques : ressources d'habillage, jamais le corps d'un article.
CHEMINS_A_IGNORER = re.compile(
    r"^/(cdn-cgi|_next/static|_nuxt|static|assets|dist|build|media|"
    r"wp-content|wp-includes|wp-json)/", re.IGNORECASE)

MOTIF_URL = re.compile(rb"https?://[^\s\"'<>\\\x00-\x1f]{12,}")
MOTIF_ANCRE = re.compile(rb'data-n-au="([^"]+)"')


# --------------------------------------------------------------- utilitaires

def identifiant(url):
    """Cle stable derivee de l'URL, pour retrouver un element d'un jour a l'autre."""
    h = 5381
    s = (url or "").strip().lower()
    s = re.sub(r"^https?://", "", s).rstrip("/")
    for c in s:
        h = ((h << 5) + h + ord(c)) & 0xFFFFFFFF
    return "u" + format(h, "x")


def recuperer(url, ua=UA_FLUX, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def texte(noeud, defaut=""):
    return (noeud.text or defaut).strip() if noeud is not None else defaut


def url_plausible(candidat):
    """Vrai si l'adresse peut etre celle d'un article de presse.

    Le critere est volontairement severe : un lien faux est bien pire qu'un lien
    non resolu, puisqu'il mene le lecteur ailleurs sans prevenir.
    """
    if not candidat or len(candidat) > 2000:
        return False
    try:
        p = urlparse(candidat)
    except ValueError:
        return False
    if p.scheme not in ("http", "https") or not p.netloc:
        return False
    hote = p.netloc.split("@")[-1].split(":")[0]
    if HOTES_A_IGNORER.search(hote) or "." not in hote:
        return False
    if len(p.path) < 2:                 # une page d'accueil n'est pas un article
        return False
    if EXTENSIONS_A_IGNORER.search(p.path):
        return False
    if CHEMINS_A_IGNORER.search(p.path):
        return False
    return True


# --------------------------------------------------------------- resolution

def _chaines_delimitees(blob):
    """Extrait les chaines longueur-prefixee du blob binaire.

    L'identifiant decode est un message au format protobuf : chaque chaine y est
    precedee d'un marqueur de champ puis de sa longueur. Lire cette longueur evite
    de deviner ou l'URL s'arrete, car un octet de structure peut etre un caractere
    parfaitement legal dans une adresse.
    """
    n = len(blob)
    i = 0
    while i < n:
        if blob[i] & 0x07 == 0x02:          # champ de type « longueur prefixee »
            j, longueur, decalage, complet = i + 1, 0, 0, False
            while j < n and decalage <= 28:
                octet = blob[j]
                j += 1
                longueur |= (octet & 0x7F) << decalage
                if not octet & 0x80:
                    complet = True
                    break
                decalage += 7
            if complet and 0 < longueur <= n - j:
                yield blob[j:j + longueur]
                i = j
                continue
        i += 1


def _par_decodage(lien):
    """Voie 1 : l'identifiant du lien encode parfois l'URL reelle en base64."""
    m = re.search(r"/(?:rss/)?articles/([A-Za-z0-9_\-]+)", lien)
    if not m:
        return None
    brut = m.group(1)
    brut += "=" * (-len(brut) % 4)
    try:
        blob = base64.urlsafe_b64decode(brut)
    except (binascii.Error, ValueError):
        return None

    for morceau in _chaines_delimitees(blob):
        try:
            candidat = morceau.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if candidat.startswith(("http://", "https://")) and url_plausible(candidat):
            return candidat

    # Repli : certains identifiants ne suivent pas cette structure.
    for trouve in MOTIF_URL.findall(blob):
        candidat = trouve.decode("utf-8", "ignore").rstrip("\\\u0001\u0002")
        if url_plausible(candidat):
            return candidat
    return None


def _par_page(lien):
    """Voie 2 : la page de redirection porte l'adresse reelle dans son HTML.

    Seul l'attribut que Google reserve a l'adresse d'origine est lu. Balayer tout
    le HTML a la recherche de la premiere adresse venue ramene les scripts de
    mesure d'audience et les ressources d'habillage : mieux vaut ne rien resoudre.
    """
    try:
        html = recuperer(lien, ua=UA_PAGE, timeout=20)
    except (urllib.error.URLError, OSError):
        return None

    for trouve in MOTIF_ANCRE.findall(html):
        candidat = trouve.decode("utf-8", "ignore")
        if url_plausible(candidat):
            return candidat
    return None


def resoudre(lien):
    """Rend (url, resolu). Conserve le lien d'origine si la resolution echoue."""
    if "news.google.com" not in lien:
        return lien, True
    for voie in (_par_decodage, _par_page):
        try:
            trouve = voie(lien)
        except Exception as e:                      # une voie qui casse ne doit rien bloquer
            print("resolution ({}) : {}".format(voie.__name__, e), file=sys.stderr)
            trouve = None
        if trouve:
            return trouve, True
    return lien, False


# --------------------------------------------------------------- flux

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
            "id": identifiant(lien),      # fonde sur le lien Google : reste stable
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


# --------------------------------------------------------------- programme

def main():
    maintenant = datetime.now(timezone.utc)
    aujourdhui = maintenant.date().isoformat()
    limite = maintenant - timedelta(days=FENETRE_JOURS)

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
    jours = {j["date"]: j for j in archive["jours"]
             if isinstance(j, dict) and j.get("date")}

    connus, titres_connus = set(), set()
    for j in jours.values():
        for it in j.get("items", []):
            connus.add(it.get("id") or identifiant(it.get("url", "")))
            titres_connus.add(
                re.sub(r"[^a-z0-9]+", "", (it.get("title") or "").lower())[:90])

    # --- reparation des resolutions erronees ---
    # Une version precedente pouvait retenir l'adresse d'un script de mesure
    # d'audience a la place de l'article. On rend a ces entrees leur lien Google
    # et on les remet dans la file des liens a resoudre.
    reparees = 0
    for j in jours.values():
        for it in j.get("items", []):
            lien = it.get("url", "")
            if not lien or "news.google.com" in lien:
                continue
            if url_plausible(lien):
                continue
            origine = it.get("gnews") or ""
            if origine:
                it["url"] = origine
                it["gnews"] = ""
            it["resolu"] = False
            reparees += 1

    jour = jours.setdefault(aujourdhui, {"date": aujourdhui, "items": []})
    jour.setdefault("items", [])
    deja = {it.get("id") for it in jour["items"]}

    ajoutes, resolus = 0, 0
    for it in frais:
        cle_titre = re.sub(r"[^a-z0-9]+", "", it["title"].lower())[:90]
        if it["id"] in connus or it["id"] in deja:
            continue
        if cle_titre and cle_titre in titres_connus:
            continue

        url_finale, ok = resoudre(it["url"])
        if ok and url_finale != it["url"]:
            resolus += 1

        jour["items"].append({
            "id": it["id"],
            "title": it["title"],
            "url": url_finale,
            "gnews": it["url"] if url_finale != it["url"] else "",
            "resolu": bool(ok),
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

    # --- nouvelle tentative sur les anciens liens non resolus ---
    rattrapes, budget = 0, RETENTATIVES_RESOLUTION
    for d in sorted(jours, reverse=True):
        for it in jours[d].get("items", []):
            if budget <= 0:
                break
            if it.get("resolu"):
                continue
            lien = it.get("gnews") or it.get("url", "")
            if "news.google.com" not in lien:
                it["resolu"] = True
                continue
            budget -= 1
            url_finale, ok = resoudre(lien)
            if ok and url_finale != lien:
                it["gnews"] = lien
                it["url"] = url_finale
                it["resolu"] = True
                rattrapes += 1
        if budget <= 0:
            break

    jour["collecte"] = maintenant.isoformat(timespec="seconds")
    jour["flux"] = [{"cle": r["cle"], "etat": r["etat"], "retenus": r["retenus"]}
                    for r in rapport]

    plancher = (maintenant - timedelta(days=ARCHIVE_JOURS)).date().isoformat()
    liste = [j for d, j in jours.items() if d >= plancher]
    liste.sort(key=lambda j: j["date"], reverse=True)

    total = sum(len(j.get("items", [])) for j in liste)
    non_resolus = sum(1 for j in liste for it in j.get("items", [])
                      if not it.get("resolu"))

    with open(ARCHIVE, "w", encoding="utf-8") as f:
        json.dump({"maj": maintenant.isoformat(timespec="seconds"),
                   "jours_couverts": len(liste), "total": total,
                   "liens_non_resolus": non_resolus, "jours": liste},
                  f, ensure_ascii=False, indent=1)

    print("{} nouvelle(s) reference(s) au {} ; {} lien(s) resolu(s), "
          "{} rattrape(s), {} repare(s) ; {} references sur {} jours ; "
          "{} lien(s) encore opaque(s)"
          .format(ajoutes, aujourdhui, resolus, rattrapes, reparees,
                  total, len(liste), non_resolus))


if __name__ == "__main__":
    main()
