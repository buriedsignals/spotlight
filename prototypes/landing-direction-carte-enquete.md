# Direction DA — "Carte d'enquête"

*Status : candidat prototype. Sauvegardé pour test.*

## Noyau conceptuel

La landing est un **territoire progressivement cartographié**. Pas de brume atmosphérique (trop AI-aesthetic) : on est dans la tradition des vieilles cartes d'exploration et des diagrammes d'enquête OCCRP / ICIJ. Trois statuts visuels coexistent en permanence, et c'est leur coexistence qui fait la démonstration produit :

1. **Zones vérifiées** — cartographiées, sourcées, deux sources indépendantes par claim, archive locale ancrée.
2. **Impasses documentées** — annotées avec date de recherche, source consultée, résultat négatif. Restent à l'écran, pas effacées.
3. **Zones encore dans le flou** — terra incognita honnête, avec mention de ce qui bloque (juridiction, registre, langue).

## Positionnement DA

- **Cartographie investigative**, pas atmosphère AI.
- Pivote "tentaculaire" de *graphe de nœuds qui pulse* vers *diagramme d'investigation à l'encre* (façon Azerbaijani Laundromat).
- "Documenter les impasses" = axe de positionnement central, méthodologiquement vrai (Bellingcat/GIJN/OCCRP), totalement original sur le marché.

## Vocabulaire visuel

- **Typo** : Fraunces ou Caslon + monospace pour annotations et timestamps. Optionnellement une serif cartographique type Didot pour les toponymes.
- **Palette** : papier (crème ou gris très pâle), encre noire, sépia pour les tracés. Un accent "vérifié", un accent "impasse" (gris-bleu retenu plutôt qu'un rouge warning).
- **Tout feels drawn, not rendered.** Pas de gradient, pas de glow.

## Hero proposé

Un lead brut arrive (nom d'entreprise, URL, claim). Au premier scroll, le territoire commence à se tracer :
- Une route part vers la juridiction A (sources trouvées, ancrées).
- Une autre bute sur un mur (impasse annotée — "registre chypriote fermé, consulté via Wayback 2024-03-12, aucun directeur listé").
- Une troisième reste floue.

À la fin de la hero, la carte est cohérente mais **honnête sur ses trous**. Le message produit : on ne fake pas la complétude.

## Inspirations

- Cartes OCCRP / ICIJ (Panama Papers, Azerbaijani Laundromat)
- Atlas XVIIe-XVIIIe avec "Terra Incognita"
- Reuters Graphics investigatifs
- Edward Tufte (Napoleon's march, small multiples)
- Diagrammes hand-drawn de journaux d'expédition

## Tech

- SVG + canvas léger pour les tracés animés au scroll.
- Pas de WebGL indispensable. Possible shader texture papier en ambiance, optionnel.
- Responsive : la carte desktop devient un carnet d'expédition vertical scrollable sur mobile, chaque zone = une section.

## Funnel (priorité Install)

Le territoire **est** la démo. Voir la carte se construire = comprendre le produit. CTA Install en fin de scroll : "Prends le territoire en main."

## Risques / zones à surveiller

- Demande du vrai contenu éditorial : les impasses doivent sonner vraies, sinon décoration. Probablement 2-3 cases réels de Buried Signals à adapter ou scénariser.
- Exécution cartographique = temps d'illustration non trivial.
- Rythme entre "territoire" et "texte" à calibrer pour pas perdre le lecteur.
