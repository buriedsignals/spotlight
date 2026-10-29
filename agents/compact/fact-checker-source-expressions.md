### Source expressions for independently acquired evidence (opt-in)

Only when `SOURCE_EXPRESSION_MODE` is `pilot` or `activated`, and only for sources you acquired yourself:

- Record each exact passage through the one writer (`source_expressions`, action `write`), never with file tools; `data/source-expressions.json` and `data/source-positions.json` refuse file writes. Never recreate or alter an investigator's passage or evidence item.
- Select only a passage you inspected in a stored case file, in its original language and exactly as written, with an exact line range or JSON Pointer. Never invent a passage, locator, hash, derivative, attribution or source; if no exact stored anchor exists, record the gap and keep the verdict within its confidence cap.
- Anchor it to your own evidence-bundle item (`created_by: fact-checker`), link it `supports`, `contradicts` or `context`, and put refs for contradicting passages on the `evidence_against` item.

Before your first passage write, read `agents/references/fact-checker-source-expressions.md`: it gives every field of the proposal, derivatives, translations and positions.

