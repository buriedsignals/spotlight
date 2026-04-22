# Direction DA — "Mise au jour"

*Status : candidat prototype. Sauvegardé pour test.*

## Noyau conceptuel

Pas de carte. Pas de graphe. Pas de brouillard rendu. **Une page.** Le visuel est porté par un seul levier : **le contraste typographique comme état de connaissance**.

Chaque fragment de texte porte un statut épistémique exprimé par son contraste et sa matière :

- **Inconnu / non encore investigué** — contraste très bas, presque invisible. Le "flou" du départ, sans brume, sans blur — juste de l'encre non encore posée.
- **En cours** — contraste moyen, italique ou entre crochets `[à vérifier]`. Marques provisoires du journaliste.
- **Vérifié** — encre pleine, sourcé.
- **Impasse documentée** — encre pleine aussi, mais rayée ou annotée, avec date de recherche et source négative. L'impasse est aussi visible que la vérité.

## Le geste "tentaculaire"

Au scroll, le texte **rayonne** depuis un point central (le lead) vers les bords de la page. Pas d'arêtes de graphe — **colonisation typographique de l'espace**. Des colonnes de texte poussent dans plusieurs directions, chacune est une piste d'enquête. Certaines s'épaississent jusqu'à l'encre pleine. Certaines butent et se figent en impasse annotée. Certaines restent pâles.

## L'arc

- **Hero** : un lead seul, en encre pleine, au centre d'une page presque vide. Autour, du texte très pâle qu'on devine sans lire — toutes les questions possibles. Le "brouillard" est **la page avant l'enquête**.
- **Milieu** : Spotlight pousse. Les contrastes se différencient. Des blocs deviennent pleins, d'autres se figent annotés, d'autres restent pâles.
- **Fin** : composition dense, avec zones pleines, impasses datées, trous honnêtement pâles. La page est **mise au jour** — pas entièrement résolue, systématiquement couverte.

## Vocabulaire visuel

- **Typo** : Fraunces ou Caslon + monospace sobre pour annotations/timestamps. Aucune display font exotique — c'est le contraste qui porte la forme.
- **Palette** : papier + encre. Accent chaud minimal pour sources vivantes. Accent discret pour impasses. Pas de gradient, pas de glow, **aucune brume**.
- **Mouvement** : le texte ne "respire" pas, il se **pose**. Chaque transition de contraste est une action visible.

## Positionnement DA

- **Plus abstrait** que Carte d'enquête : rien n'est figuré.
- **Non-caricatural** : ni brume, ni faisceau, ni graphe.
- **Anti-AI par construction** : l'esthétique AI est glow/gradient/particules ; ici c'est contrôle du contraste et matière encre/papier.
- **Embody le produit** : l'impasse documentée est visuellement co-égale à la vérité — démonstration de rigueur maximale.

## Tech

100% DOM + CSS + scroll-driven animations (JS léger). Variable font pour animer les transitions de contraste sans paliers durs. Zéro WebGL. Techniquement le plus simple, éditorialement le plus exigeant à **composer**.

## Risques

- Subtil — un visiteur pressé peut rater l'idée si le contraste initial est trop bas. Hero doit avoir un point d'accroche fort.
- Très dépendant du contenu — exige un texte d'investigation vrai ou scénarisé avec soin.
- Mauvais kerning, mauvais rythme = tout tombe à plat. Demande du temps de composition.
