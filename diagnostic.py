#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Essaie plusieurs formes de requete et compte ce que Google rend.

Sert a trouver pourquoi une requete ne rapporte rien. Ne touche a aucun
fichier : il affiche seulement un tableau dans le journal de l'execution.
"""
import urllib.request, urllib.error
from urllib.parse import quote
from xml.etree import ElementTree

BASE = "https://news.google.com/rss/search?q={}&hl=fr&gl=FR&ceid=FR:fr"
UA = "veille-gem/3.0 (+github actions)"

ESSAIS = [
    # --- terme-exact : la seule qui rapporte aujourd'hui ---
    ("A1 terme-exact actuel",
     '("groupe d\'entraide mutuelle" OR "groupes d\'entraide mutuelle") when:14d'),
    ("A2 sans parentheses",
     '"groupe d\'entraide mutuelle" OR "groupes d\'entraide mutuelle" when:14d'),
    ("A3 singulier seul",
     '"groupe d\'entraide mutuelle" when:14d'),
    ("A4 singulier sans when",
     '"groupe d\'entraide mutuelle"'),
    ("A5 when:30d",
     '"groupe d\'entraide mutuelle" OR "groupes d\'entraide mutuelle" when:30d'),

    # --- sigle-qualifie : rend zero ---
    ("B1 actuel",
     'GEM ("sante mentale" OR "handicap psychique" OR "pair-aidance") '
     '-"Grenoble Ecole de Management" -Gemenos -festival when:14d'),
    ("B2 sans exclusions",
     'GEM ("sante mentale" OR "handicap psychique" OR "pair-aidance") when:14d'),
    ("B3 sans when",
     'GEM ("sante mentale" OR "handicap psychique" OR "pair-aidance")'),
    ("B4 deux mots simples",
     'GEM "sante mentale" when:14d'),
    ("B5 GEM seul",
     'GEM when:14d'),
    ("B6 avec accents",
     'GEM ("santé mentale" OR "handicap psychique") when:14d'),

    # --- federation : rend zero ---
    ("C1 actuel", '("FIGEM" OR "CNIGEM") when:14d'),
    ("C2 FIGEM seul", 'FIGEM when:30d'),
    ("C3 FIGEM sans when", 'FIGEM'),

    # --- temoins ---
    ("T1 temoin large", 'psychiatrie when:2d'),
    ("T2 temoin exclusions", 'psychiatrie -hopital when:2d'),
]


def compter(requete):
    url = BASE.format(quote(requete, safe=""))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            brut = r.read()
    except (urllib.error.URLError, OSError) as e:
        return "erreur", str(e)[:50]
    try:
        racine = ElementTree.fromstring(brut)
    except ElementTree.ParseError as e:
        return "xml", str(e)[:50]
    items = list(racine.iter("item"))
    premier = ""
    if items:
        t = items[0].find("title")
        premier = (t.text or "")[:52] if t is not None else ""
    return len(items), premier


print("{:28s} {:>7s}  {}".format("essai", "items", "premier titre"))
print("-" * 100)
for nom, requete in ESSAIS:
    n, info = compter(requete)
    print("{:28s} {:>7}  {}".format(nom, n, info))
print("-" * 100)
for nom, requete in ESSAIS:
    print("{:28s} {}".format(nom, requete))
