#!/usr/bin/env python3
"""Stand-in for Engine's `bsig --json wiki case-search`, shared by
tests/case-search-check.py, harness/pi/src/launcher.test.ts and
harness/review/test. It prints the NDJSON contract of Engine's wiki verb
(progress events, then one `result` event whose data.result carries claim,
ranking, embeddings, note and passages of path, chunk, text, score; or one
`error` event and exit 1).

Copy it to a path P and write its scenario to P + ".json" (Engine gets a
minimal environment, so the scenario cannot travel in variables):
  mode       ok | off | fail | huge | slow   (default ok)
  passages   list of {path, chunk, text, score}; "{claim}" in text is replaced
  fail_from  with mode ok: calls from this number on (1-based) fail
  args_log   file that gets one JSON line of argv per call
  env_log    file that gets the environment's variable names per call
  pid_log    file that gets {"pid", "start"} when a call starts and
             {"pid", "end"} when it answers
  slow_s     with mode slow: seconds to wait before answering
"""
import json
import os
import sys
import time

scenario_path = os.path.abspath(sys.argv[0]) + ".json"
try:
    with open(scenario_path, encoding="utf-8") as handle:
        scenario = json.load(handle)
except FileNotFoundError:
    scenario = {}
args = sys.argv[1:]
call = 1
if scenario.get("args_log"):
    try:
        with open(scenario["args_log"], encoding="utf-8") as handle:
            call = sum(1 for _ in handle) + 1
    except FileNotFoundError:
        pass
    with open(scenario["args_log"], "a", encoding="utf-8") as out:
        out.write(json.dumps(args) + "\n")
if scenario.get("env_log"):
    with open(scenario["env_log"], "a", encoding="utf-8") as out:
        out.write(json.dumps(sorted(os.environ)) + "\n")

def mark(**record):
    if scenario.get("pid_log"):
        with open(scenario["pid_log"], "a", encoding="utf-8") as out:
            out.write(json.dumps({"pid": os.getpid(), **record}) + "\n")


mark(start=time.time())
mode = scenario.get("mode", "ok")
if scenario.get("fail_from") and call >= scenario["fail_from"]:
    mode = "fail"
if mode == "off":
    print(json.dumps({"event": "error", "message": "wiki: case semantic search is off (wiki.Config.CaseSearch): its U16 ship gate is not met"}))
    sys.exit(1)
if mode == "fail":
    sys.stderr.write("panic: provider rejected key sk-or-v1-STANDIN-SECRET at /Users/someone/private/path\n")
    print(json.dumps({"event": "error", "message": "provider rejected key sk-or-v1-STANDIN-SECRET"}))
    sys.exit(1)
if mode == "slow":
    time.sleep(float(scenario.get("slow_s", 30)))
if mode == "huge":
    chunk = "x" * 65536
    while True:
        sys.stdout.write(chunk)
        sys.stdout.flush()

claim = args[args.index("--claim") + 1]
passages = scenario.get("passages") or [
    {"path": "research/registry.md", "chunk": 0, "text": "Registry entry on {claim}", "score": 12.5},
]
passages = [{**p, "text": p["text"].replace("{claim}", claim)} for p in passages]
print(json.dumps({"event": "progress", "message": "indexing"}))
print(json.dumps({"event": "result", "message": "wiki case-search", "data": {"tool": "case-search", "result": {
    "claim": claim, "ranking": "bm25", "embeddings": False,
    "note": "the embedding helper is not running; ranked by words (BM25) only",
    "passages": passages, "retrieval": "retrieval only"}}}))
mark(end=time.time())
