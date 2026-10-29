### Source Expressions (opt-in pilot and activated cases)

Only when `SOURCE_EXPRESSION_MODE` is `pilot` or `activated`. For each exact passage that supports, contradicts or contextualizes a finding:

- Record it through the one writer (`source_expressions`, action `write`), never with file tools: `data/source-expressions.json` and `data/source-positions.json` refuse file writes.
- Select only a passage you inspected in a stored case file, in its original language and exactly as written, with an exact line range or JSON Pointer. Never invent text, a locator, a hash, an attribution or a derivative; if the exact passage cannot be recovered, emit no expression and record the gap.
- Link it to each finding it bears on (`supports`, `contradicts` or `context`), and give every attributed finding a source position.
- After writing `findings.json` in `activated` mode, set the finding fingerprints with `source_expressions` action `fingerprint_findings`; never compute them yourself.

Before your first passage write, read `agents/references/investigator-source-expressions.md`: it gives every field of the proposal, derivatives, translations, positions and lifecycle.

