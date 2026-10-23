# veille-gem

Veille quotidienne sur les groupes d'entraide mutuelle : collecte automatique,
archive jour par jour, et site de consultation. Le tout hébergé par ce dépôt,
sans service tiers ni abonnement.

## Comment cela fonctionne

```
Google Actualités  ──►  GitHub Actions  ──►  jours.json  ──►  GitHub Pages
   (3 requêtes)          chaque matin         l'archive        le site
                                                   ▲
                                                   │
                                       tâche planifiée Claude
                                     (résumés et tri, facultatif)
```

La collecte et le site ne dépendent de rien d'autre que de GitHub. Si la tâche
planifiée s'arrête, le site continue de se remplir : les références s'affichent
avec leur titre, sans résumé.

Google Actualités interdit la lecture de ses flux RSS aux robots, et la même
restriction s'applique aux flux Google Alertes et aux feuilles Google publiées.
Un exécuteur GitHub Actions, lui, n'a pas cette contrainte.

## Les fichiers

| Fichier | Rôle |
|---|---|
| `collecte.py` | interroge les trois flux, écarte les faux positifs, alimente l'archive |
| `.github/workflows/veille.yml` | lance la collecte chaque matin en semaine |
| `jours.json` | l'archive jour par jour, lue par le site |
| `veille.json` | la collecte brute du jour, utile pour diagnostiquer |
| `index.html` | le site, une page sans dépendance |

## Mise en place

1. Dépôt **public** : les minutes d'exécution y sont gratuites et sans limite.
2. **Settings → Actions → General → Workflow permissions** : cochez
   **Read and write permissions**, sans quoi le dépôt du fichier échoue.
3. **Settings → Pages → Source** : *Deploy from a branch*, branche `main`,
   dossier `/ (root)`. Le site paraît à
   `https://<votre-compte>.github.io/veille-gem/` après une ou deux minutes.
4. **Actions → veille-gem → Run workflow** pour une première collecte.

Pour un domaine à vous, ajoutez un fichier `CNAME` contenant le nom de domaine
et faites pointer un enregistrement DNS vers GitHub Pages.

## Modifier les requêtes

Tout se règle en tête de `collecte.py` :

- `REQUETES` : les trois requêtes. La syntaxe Google Actualités accepte les
  guillemets pour une expression exacte, `OR` en majuscules, les parenthèses,
  et `-terme` pour exclure.
- `FENETRE_JOURS` : au-delà de cet âge, un élément remonté par un flux est ignoré.
- `ARCHIVE_JOURS` : profondeur conservée dans `jours.json`.
- `EXCLUSIONS` : les faux positifs du sigle GEM, filtrés sur le titre et la source.

Après modification, **Run workflow** pour voir le résultat tout de suite.

## Format de `jours.json`

```json
{
  "maj": "2026-10-26T04:42:11+00:00",
  "jours_couverts": 12,
  "total": 38,
  "jours": [
    {
      "date": "2026-10-26",
      "collecte": "2026-10-26T04:42:11+00:00",
      "flux": [ { "cle": "terme-exact", "etat": "ok", "retenus": 4 } ],
      "items": [
        {
          "id": "ufe170bb9",
          "title": "…",
          "url": "https://news.google.com/rss/articles/…",
          "source": "Sud Ouest",
          "published": "2026-10-22",
          "feed": "terme-exact",
          "summary": "",
          "statut": ""
        }
      ]
    }
  ]
}
```

Une référence est rattachée au jour où elle a été vue pour la première fois, et
n'est jamais inscrite deux fois : la déduplication porte sur l'`id`, dérivé de
l'URL, et sur le titre normalisé.

Deux champs sont laissés vides par la collecte et ne sont **jamais écrasés** :

- `summary` : un résumé d'une ou deux phrases, rédigé et non copié de la source.
- `statut` : `""`, `"retenu"` ou `"hors-sujet"`. C'est le tri partagé, visible
  de tous les lecteurs du site.

Vous pouvez les remplir à la main dans le fichier. Une tâche planifiée peut aussi
s'en charger chaque matin.

## Marquage à la lecture

Les boutons « Retenir » et « Hors sujet » du site enregistrent le choix dans le
navigateur du lecteur. Ce marquage ne suit pas d'un appareil à l'autre et n'est
pas vu des autres. Le tri partagé, lui, est le champ `statut` du fichier.

## Limite connue

Les liens rendus par Google Actualités passent par une redirection
`news.google.com/rss/articles/…` plutôt que par l'adresse de l'article. Ils
s'ouvrent correctement, mais le média ne se lit pas dans l'URL : il est donc
conservé à part, dans le champ `source`.

## Coût

Nul. Dépôt public, minutes d'exécution illimitées, GitHub Pages gratuit.
La collecte dure quelques secondes.
