#!/usr/bin/env python3
"""osint-tools — local OSINT tool discovery over a SQLite+FTS5 index of the open tool database.

Replaces the always-on inline catalogue (skills/osint/SKILL.md) and the ~11.8k-token force-read
reference (skills/osint/references/tools-by-category.md) with a targeted local query. Same discovery
role as the OSINT Navigator API, but offline, deterministic, and available to every tier — Navigator
stays as the entitlement-gated first pass for subscription deployments (see PRD R1/R2).

Two subcommands:
  build   pull the HF parquet once (install-time) and build the FTS5 index. Needs huggingface_hub +
          pandas + pyarrow — run via:  uv run --with huggingface_hub --with pandas --with pyarrow \
                                         scripts/osint-tools.py build
  find    query the index. STDLIB ONLY (sqlite3) so the harness hot-path has zero deps and is instant.

DB location: $SPOTLIGHT_OSINT_DB, else ~/.spotlight/osint_tools.db
Dataset:     $SPOTLIGHT_OSINT_DATASET, else tomvaillant/osint-tool-database (12,500 tools)
"""
import argparse, os, sqlite3, sys, json

DEFAULT_DB = os.environ.get("SPOTLIGHT_OSINT_DB",
                            os.path.expanduser("~/.spotlight/osint_tools.db"))
DEFAULT_DATASET = os.environ.get("SPOTLIGHT_OSINT_DATASET", "tomvaillant/osint-tool-database")

# FTS is a keyword index; the caller passes lead-derived terms. Categories are an enum the skill
# publishes so a weak model can scope queries. (Populated from the dataset at build time.)


def _flat(v):
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return "" if v is None else str(v)


def build(args):
    """Install-time: pull the dataset and (re)build the SQLite + FTS5 index."""
    try:
        from datasets import load_dataset
    except ImportError:
        sys.exit("build needs `datasets` — run: uv run --with datasets --with pandas "
                 "scripts/osint-tools.py build")
    print(f"[osint-tools] loading {args.dataset} ...", flush=True)
    rows = load_dataset(args.dataset, split="train").to_pandas()
    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    if os.path.exists(args.db):
        os.remove(args.db)
    con = sqlite3.connect(args.db)
    cur = con.cursor()
    cur.execute("""CREATE TABLE tools(tool_name TEXT, tool_url TEXT, category TEXT,
                   short_description TEXT, source_toolkits TEXT, status TEXT, last_updated TEXT)""")
    cur.executemany(
        "INSERT INTO tools VALUES (?,?,?,?,?,?,?)",
        [(_flat(r.tool_name), _flat(r.tool_url), _flat(r.category), _flat(r.short_description),
          _flat(r.source_toolkits), _flat(r.status), _flat(r.last_updated)) for r in rows.itertuples()])
    cur.execute("""CREATE VIRTUAL TABLE tools_fts USING fts5(
                   tool_name, category, short_description, source_toolkits, content='tools')""")
    cur.execute("""INSERT INTO tools_fts(rowid, tool_name, category, short_description, source_toolkits)
                   SELECT rowid, tool_name, category, short_description, source_toolkits FROM tools""")
    # publish the category enum for the skill's query scaffolding
    cats = [c for (c,) in cur.execute(
        "SELECT category FROM tools GROUP BY category ORDER BY COUNT(*) DESC").fetchall() if c]
    cur.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    cur.execute("INSERT INTO meta VALUES('categories', ?)", (json.dumps(cats),))
    cur.execute("INSERT INTO meta VALUES('n_tools', ?)", (str(len(rows)),))
    con.commit()
    con.close()
    print(f"[osint-tools] built {args.db}  ({len(rows)} tools, {len(cats)} categories, "
          f"{os.path.getsize(args.db)//1024} KB)")


def _escape_fts(q):
    # keep it forgiving for weak models: bare terms OR'd; quote anything with punctuation
    terms = [t for t in q.replace(",", " ").split() if t]
    safe = []
    for t in terms:
        if t.upper() in ("OR", "AND", "NOT"):
            safe.append(t.upper())
        elif t.isalnum():
            safe.append(t)
        else:
            safe.append('"' + t.replace('"', '') + '"')
    return " OR ".join(x for x in safe if x not in ("OR", "AND", "NOT")) or '""'


def find(args):
    """Hot path (stdlib only): FTS query, optional category scope, compact ranked output."""
    if not os.path.exists(args.db):
        sys.exit(f"[osint-tools] index missing at {args.db} — run `osint-tools build` "
                 f"(install-spotlight builds it).")
    con = sqlite3.connect(args.db)
    cur = con.cursor()
    match = _escape_fts(args.query)
    params = [match]
    where = "tools_fts MATCH ?"
    if args.category:
        where += " AND t.category = ?"
        params.append(args.category)
    # FTS is external-content (indexes text cols only); join to `tools` for url/full row.
    sql = (f"SELECT t.tool_name, t.category, t.tool_url, substr(t.short_description,1,110) "
           f"FROM tools_fts JOIN tools t ON t.rowid = tools_fts.rowid "
           f"WHERE {where} ORDER BY rank LIMIT ?")
    params.append(args.limit)
    try:
        r = cur.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        sys.exit(f"[osint-tools] query error: {e} (query={args.query!r} -> match={match!r})")
    if args.json:
        print(json.dumps([{"tool": n, "category": c, "url": u, "description": d}
                          for n, c, u, d in r], indent=2))
    else:
        if not r:
            print("(no matching tools — broaden terms or drop --category)")
        for n, c, u, d in r:
            print(f"- {n} [{c}] — {d}\n    {u}")
    con.close()


def categories(args):
    con = sqlite3.connect(args.db); cur = con.cursor()
    v = cur.execute("SELECT value FROM meta WHERE key='categories'").fetchone()
    print("\n".join(json.loads(v[0])) if v else "(no meta — rebuild)")
    con.close()


def main():
    p = argparse.ArgumentParser(description="Local OSINT tool discovery (SQLite+FTS5).")
    p.add_argument("--db", default=DEFAULT_DB)
    sub = p.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("build", help="install-time: pull dataset + build index")
    pb.add_argument("--dataset", default=DEFAULT_DATASET)
    pb.set_defaults(fn=build)

    pf = sub.add_parser("find", help="query the index for tools matching lead-derived keywords")
    pf.add_argument("query")
    pf.add_argument("--category", help="scope to one category (see `categories`)")
    pf.add_argument("--limit", type=int, default=8)
    pf.add_argument("--json", action="store_true")
    pf.set_defaults(fn=find)

    pc = sub.add_parser("categories", help="list the category enum for query scoping")
    pc.set_defaults(fn=categories)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
