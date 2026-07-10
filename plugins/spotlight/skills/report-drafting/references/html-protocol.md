# HTML editing protocol (hard rule)

NEVER run greedy regex substitution on the HTML file. Use `Read` + `Edit` with **anchored `old_string`s only**. A greedy `re.sub` destroyed the entire `report.html` mid-pass in a prior investigation and forced a full rebuild.

Specifically:
- **Per-finding additions:** anchor `old_string` on the closing element of the prior block + the opening of the target block.
- **Methodology restructuring:** extract the existing section, rewrite as a single block, replace with one `Edit` call.
- **If you must regex:** do it in a one-shot Python script that prints the diff first — never `re.sub(..., re.DOTALL)` on the whole file.

## Validation before declaring done

- Balance check: `python3 -c "from html.parser import HTMLParser; ..."` (tags balance).
- Smoke test: open `report.html` in a browser AND run the headless checks from `interactive-diagrams.md` (zero mermaid error elements, expected SVG count, screenshot inspected for clipped labels).
