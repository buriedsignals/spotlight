# Report diagram design reference

This is a small Spotlight adaptation of
[`cathrynlavery/diagram-design`](https://github.com/cathrynlavery/diagram-design)
at commit `09df49d8d1a1c7fb2efdfcdc7a2a0713534350a6` (MIT). It is a visual
reference, not a runtime dependency or a template to copy.

## Choose one structure

| Type | Use |
|---|---|
| `flow` | Money, assets, information, or influence moving between entities. |
| `hierarchy` | Ownership, control, parent/subsidiary, or command structures. |
| `network` | Directed material relationships without one hierarchy or main path. |
| `loop` | One directed cycle, such as money returning to an earlier entity. |

Use one diagram to answer one question. Split a structure before it becomes a
wall of nodes.

## Visual rules

- Use at most nine nodes, twelve connections, and two focal entities.
- Give the diagram a clear reading direction: left-to-right for flow and
  network, top-to-bottom for hierarchy, and a declared starting entity for a
  loop.
- Use short relationship labels. Preserve the recorded direction of every
  connection.
- Distinguish source, intermediary, and sink roles in a flow; root, branch,
  and leaf roles in a hierarchy. Use neutral treatment for ordinary network
  and loop nodes.
- Reserve one restrained accent for focal entities. Do not reuse verdict
  colours to communicate structural roles.
- Keep connectors simple and legible, without shadows. Put a legend outside
  the drawing area.
- Use Spotlight's report typography and colours, not upstream fonts or palette.

## Spotlight boundary

The model selects only existing `findings.json.connections` in
`report-draft.json`. It never writes Mermaid, SVG, HTML, JavaScript, CSS,
coordinates, URLs, or Mermaid configuration. The deterministic renderer
creates the diagram source, labels, layout, classes, title, and description.

For Mermaid/ELK loading, layout, and canvas behavior, see
`interactive-diagrams.md`.
