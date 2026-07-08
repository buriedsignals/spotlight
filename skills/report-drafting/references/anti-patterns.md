# Anti-patterns (learned the hard way)

- **Wall-of-text findings.** Every finding gets a `.path` block. If you're writing "we ran D11 then drilled then archived three URLs" in the prose, lift it into the path block.
- **Methodology-at-the-bottom dumping ground.** The methodology section is the run log; phases appear in phase order, with fact-check and handoff tables INSIDE the relevant phase blocks — not separate. (See `methodology-pattern.md`.)
- **Sources at the end of the document.** Sources go inline per-finding via the `.sources` strip. Readers should never scroll to a bibliography.
- **`.flag strong { display: block }`.** Breaks inline legal citations onto new lines. Use `<span class="flag-label">` instead.
- **Markdown-style HTML.** The HTML is a designed document, not a markdown render. If it reads like `pandoc` output, restart from the template.
- **Regex on the live file.** Use `Read` + `Edit` with anchored `old_string`s. A greedy substitution will destroy hours of work. (See `html-protocol.md`.)
- **Citation hallucination.** The synthesis layer must never originate a UUID, URL, docket caption, or direct quote absent from the ground-truth files. The draft that "looks right" but cites URLs that 404 / UUIDs that resolve to the wrong filing is the single most common way investigative submissions get killed. Run the closure script. (See `citation-discipline.md`.)
- **Novelty inflation.** Already published by a mainstream outlet ⇒ `.pill-connected` (outline), not `.pill-novel`. Call out the actual novel sub-element in a "Novelty" paragraph. Mislabeling a reported timeline as "novel" is a credibility hit.
