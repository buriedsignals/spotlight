# OSINT Skill — Setup Instructions

Platform-specific instructions for installing the OSINT investigation skill.

## Claude Code (Plugin Marketplace)

```bash
claude plugin marketplace add buriedsignals/skills
```

Then install the plugin:

```bash
claude plugin install investigate@buriedsignals
```

The OSINT skill loads automatically when relevant.

## LM Studio

1. Copy the contents of `SKILL.md` into **Settings → System Prompt**
2. For the full tool catalog, also paste `references/tools-by-category.md`
3. If your model has limited context (<32K), use only SKILL.md — the routing table works standalone

## Ollama

1. Create a Modelfile:

```
FROM llama3.1:8b
SYSTEM """
<paste contents of SKILL.md here>
"""
```

2. Build: `ollama create osint-investigator -f Modelfile`
3. Run: `ollama run osint-investigator`
4. For the full catalog, concatenate SKILL.md + tools-by-category.md into the SYSTEM block

## Open WebUI

1. Go to **Workspace → Documents**
2. Upload SKILL.md and the reference files
3. In a new chat, reference the documents with `#`

## Any Other LLM Interface

Copy SKILL.md into your system prompt or custom instructions field. Add reference files if your context window allows.

## Tips

- **SKILL.md alone** (~3K tokens) gives you the routing table and investigation methodology
- **Adding tools-by-category.md** (~11K tokens) gives you the full curated catalog
- **Adding all reference files** (~18K tokens total) gives the complete experience
- For models with small context windows, SKILL.md alone is sufficient
