# Direction DA — "Marginalia"

*Status : candidat prototype. Sauvegardé pour test.*

## Noyau conceptuel

La landing est structurée comme **une édition critique** : colonne principale + marge vivante de citations et de sources à droite. Chaque affirmation dans la colonne principale est sourcée en temps réel dans la marge. Au scroll, les sources accompagnent, se déploient parfois en mini-documents, se cross-référencent entre elles. **La marge est co-sujet, pas décoration.**

L'interface embodie la méthodologie du produit (sourcing rigoureux, fact-check indépendant, chain of custody). La forme démontre ce que le produit fait.

## Positionnement DA

- **Anti-landing-AI** par construction : les landings AI cachent leurs sources (parce qu'il n'y en a pas). Celle-ci les exhibe avec fierté.
- **Anti-caricature investigation** : pas de magnifying glass, pas de corkboard, pas de thread rouge, pas de noir thriller.
- Le moat visuel est **structurel** (architecture à marge vivante) plutôt que **stylistique** (palette, fonts) — donc non-copiable sans repenser tout son site.

## Vocabulaire visuel

- **Typo** : Fraunces (text, pas display) ou Source Serif en corps ; monospace discret pour IDs, URLs, hashes, timestamps.
- **Palette** : papier + encre + un accent chaud unique pour les liens vivants entre colonne et marge.
- **Pas de** : gradients, glows, particules qui respirent, graphes 3D, terminal prompts en hero, gradient AI dark.

## Hero proposé

Un lead arrive **sans aucune source** (colonne principale seule, marge vide). Au scroll, la marge se peuple : deux sources indépendantes par claim, fact-checker qui tranche, archive locale (Wayback/Archive.today), entités cross-linkées avec le vault. **La méthodologie est le mouvement de la page.**

## Inspirations

- Sidenotes d'Edward Tufte
- The New York Review of Books digital
- Robin Sloan's newsletters
- Éditions critiques Harvard Digital Collections
- Are.na traité en mode scholarly

## Tech

- 100% DOM/CSS + Intersection Observers pour la sync scroll entre colonnes.
- Zéro WebGL requis.
- Responsive : sur mobile, la marge devient des chips inline sous chaque claim, qui se déploient au tap.

## Funnel (priorité Install)

Chaque scène démontre une capacité produit via la marge vivante :
1. **Hero** — lead sans source → marge se peuple (sourcing)
2. **Cycle** — une claim contestée → fact-checker indépendant tranche dans la marge (verdict)
3. **Chain of custody** — chaque source a son archive locale visible (evidence grounding)
4. **Vault** — les entités cross-linkent vers des cases passés (memory)
5. **Close** — "Prends le relais" → CTA Install

## Risques / zones à surveiller

- Exécution : si la marge est mal synchronisée ou illisible, tout s'effondre. Sync scroll = délicat.
- Mobile : la dégradation en chips inline doit être aussi soignée que la version desktop, sinon 50% des visiteurs manquent le concept.
- Contenu éditorial : la colonne principale + marge demande du vrai contenu écrit (pas de lorem). Probablement un brief rédactionnel à tirer de Tom en parallèle.
