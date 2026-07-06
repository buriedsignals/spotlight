#!/usr/bin/env python3
"""Validate the per-agent skill composition map (PRD D2 / loop U2).

Checks the three U2 test scenarios and projects the per-agent context floor
under composition + on-invoke loading vs the current "load everything" floor.

Run from the spotlight repo root:  python3 harness/validate_composition.py
Exit 0 = all scenarios pass.
"""
import json
import os
import re
import sys
import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def approx_tokens(text: str) -> int:
    return len(text) // 4  # char/4 approx, same yardstick as the U1 baseline


def split_frontmatter(md: str):
    m = re.match(r"^---\n(.*?)\n---\n", md, re.S)
    if not m:
        return "", md
    fm = m.group(1)
    desc = ""
    for line in fm.splitlines():
        mm = re.match(r"^description:\s*(.*)$", line)
        if mm:
            desc = mm.group(1).strip()
    return desc, md[m.end():]


def main() -> int:
    comp = json.load(open(os.path.join(ROOT, "harness/composition.json")))
    resolved = {
        os.path.basename(os.path.dirname(f))
        for f in glob.glob(os.path.join(ROOT, "skills/*/SKILL.md"))
    }
    agents = comp["agents"]

    # Precompute skill body + description token sizes.
    desc_tok, body_tok = {}, {}
    for name in resolved:
        md = open(os.path.join(ROOT, "skills", name, "SKILL.md")).read()
        d, body = split_frontmatter(md)
        desc_tok[name] = approx_tokens(d) or 20  # floor a description at ~20 tok
        body_tok[name] = approx_tokens(body)

    ok = True

    # (a) every skill id in the map exists in the resolved set
    for ag, spec in agents.items():
        for s in spec["skills"]:
            if s not in resolved:
                print(f"FAIL (a): {ag} references non-existent skill '{s}'")
                ok = False

    # (b) union of bundles subset of resolved set
    union = set().union(*(set(spec["skills"]) for spec in agents.values()))
    if not union <= resolved:
        print(f"FAIL (b): bundles reference skills outside resolved set: {union - resolved}")
        ok = False

    # (c) each bundle is non-empty and materially smaller than the whole
    full = len(resolved)
    for ag, spec in agents.items():
        n = len(spec["skills"])
        if n == 0:
            print(f"FAIL (c): {ag} bundle is empty")
            ok = False
        elif n >= full:
            print(f"FAIL (c): {ag} bundle ({n}) not smaller than the whole ({full})")
            ok = False

    print(f"\nScenarios (a) exists, (b) subset, (c) non-empty & smaller: {'PASS' if ok else 'FAIL'}")
    print(f"Union covers {len(union)}/{full - 1} non-orchestrator skills "
          f"(README.md is not a skill).\n")

    # Floor projection: role + bundle DESCRIPTIONS (bodies load on invoke).
    role_tok = {ag: approx_tokens(open(os.path.join(ROOT, spec["role"])).read())
                for ag, spec in agents.items()}
    all_bodies = sum(body_tok.values()) - body_tok.get("spotlight", 0)  # 15 on-invoke bodies
    old_floor = role_tok["investigator"] + all_bodies  # heaviest agent, old model
    print(f"{'agent':<16}{'role~tok':<10}{'+bundle desc~tok':<18}{'= manifest floor':<18}")
    for ag, spec in agents.items():
        d = sum(desc_tok[s] for s in spec["skills"])
        print(f"{ag:<16}{role_tok[ag]:<10}{d:<18}{role_tok[ag] + d}")
    print(f"\nOLD floor (an agent + ALL 15 bodies up-front): ~{old_floor} tok")
    worst_new = max(role_tok[ag] + sum(desc_tok[s] for s in agents[ag]["skills"]) for ag in agents)
    print(f"NEW worst-agent manifest floor (composition + on-invoke): ~{worst_new} tok  "
          f"(target ≤ ~20K) -> {'MEETS' if worst_new <= 20000 else 'MISSES'} target")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
