#!/usr/bin/env python3
"""Offline checks for decision-model signals (OpenRouter Decisions API).

No network: every test uses FakeProvider or a fake HTTP opener.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "integrations"))
sys.path.insert(0, str(ROOT / "integrations" / "decisions"))

import client  # noqa: E402
import decision_signals_lib as lib  # noqa: E402
import questions  # noqa: E402
import rules  # noqa: E402


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


signals_cli = load("decision_signals_cli", ROOT / "scripts" / "decision-signals.py")
render_check = load("render_report_check", ROOT / "tests" / "render-report-check.py")


# ------------------------------------------------------------------ fake answers
GOOD_WHEN_YES = ("present the claimed situation", "give that complete list")


def clean_answer(question: dict) -> dict:
    """A 'nothing wrong' answer for any question in the bank."""
    if question["type"] == "noul":
        good = any(marker in question["instructions"] for marker in GOOD_WHEN_YES)
        return {"type": "noul", "noul": 0.95 if good else 0.05}
    options = list(question["criteria"])
    preferred = next((o for o in ("direct", "not_applicable", "asserted", "faithful", "none", "equivalent",
                                  "supports", "company", "same_claim") if o in options), options[0])
    probs = {o: (0.96 if o == preferred else round(0.04 / (len(options) - 1), 4)) for o in options}
    return {"type": "choice", "choice": preferred, "probabilities": probs, "confidence": 0.9}


def provider_with(overrides: dict | None = None, raw: dict | None = None) -> client.FakeProvider:
    """overrides: base question id -> value for both orders; raw: exact question id -> answer."""
    overrides = overrides or {}
    raw = raw or {}

    def answer(state, qid, question):
        if qid in raw:
            return raw[qid]
        base = qid[:-4] if qid.endswith("_rev") else qid
        if base in overrides:
            value = overrides[base]
            if question["type"] == "noul":
                return {"type": "noul", "noul": value}
            options = list(question["criteria"])
            probs = {o: (0.9 if o == value else round(0.1 / (len(options) - 1), 4)) for o in options}
            return {"type": "choice", "choice": value, "probabilities": probs, "confidence": 0.8}
        return clean_answer(question)

    return client.FakeProvider(answer)


def fresh_case(tmp: Path) -> Path:
    return render_check.build_case(tmp)


def config(tmp: Path, modes: dict, name: str = "config.json") -> Path:
    path = tmp / name
    path.write_text(json.dumps({"integrations": {"decisions": {"enabled": True, "modes": modes}}}))
    return path


def run_phase(case: Path, cfg: Path, phase: str, provider, extra: list[str] | None = None) -> dict:
    out = io.StringIO()
    stdout, sys.stdout = sys.stdout, out
    try:
        code = signals_cli.main([str(case), "--phase", phase, "--config", str(cfg), "--case-opt-in", *(extra or [])], provider=provider)
    finally:
        sys.stdout = stdout
    assert code == 0, out.getvalue()
    return json.loads(out.getvalue().strip().splitlines()[-1])


def run_script(name: str, case: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / name), str(case)], capture_output=True, text=True)


def signals(case: Path, name: str = lib.SIGNALS_NAME) -> dict:
    return json.loads((case / "data" / name).read_text())


def ledger_claim(case: Path, fid: str) -> dict:
    return next(c for c in json.loads((case / "evidence-map.json").read_text())["claims"] if c["id"] == fid)


# ------------------------------------------------------------------ unit tests
def test_client_boundary() -> None:
    for args in ({"api_key": "key", "sensitive": True}, {"api_key": ""}):
        try:
            client.JevOpenRouterProvider(**args)
            raise AssertionError("sensitive mode and a missing key must block construction")
        except client.DecisionUnavailable:
            pass
    sent = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    question = {"q": {"type": "noul", "instructions": "?", "criteria": {"true": "y", "false": "n"}}}

    def opener(request, timeout):
        sent.append(json.loads(request.data))
        if len(sent) == 1:
            raise HTTPError(request.full_url, 429, "rate", {}, None)
        return Response(json.dumps({"model": "m", "answers": {"q": {"type": "noul", "noul": 0.5}}, "usage": {}}).encode())

    client.JevOpenRouterProvider("key", opener=opener, retries=1).decide({"x": 1}, question)
    assert len(sent) == 2, "429 must be retried"
    assert sent[0]["provider"] == {"zdr": True, "allow_fallbacks": False}, "zero data retention must be pinned"
    for bad in ({"answers": {}}, {"answers": {"q": None}}, {"answers": {"q": {"type": "noul", "noul": 1.7}}}):
        try:
            client.JevOpenRouterProvider("key", opener=lambda r, timeout, b=bad: Response(json.dumps(b).encode()), retries=0).decide({}, question)
            raise AssertionError("missing or malformed answers must be unavailable, never guessed")
        except client.DecisionUnavailable:
            pass
    choice = {"type": "choice", "instructions": "?", "criteria": {"a": "", "b": ""}}
    assert client.answer_problem(choice, {"choice": "c", "probabilities": {"a": 1}}) is not None
    assert client.answer_problem(choice, {"choice": "a", "probabilities": {"a": 0.9, "z": 0.1}}) is not None
    assert client.answer_problem(choice, {"choice": "a", "probabilities": {"a": 0.9, "b": 0.1}}) is None


def test_rules() -> None:
    bank = questions.ALL["grounding"]
    clean = {qid: clean_answer(q) for qid, q in bank.items()}
    assert rules.grounding_signal("X is CEO.", clean)["cap"] == "none"
    routed = rules.grounding_signal("X paid 3 + 4 million.", dict(clean, g_arithmetic={"type": "noul", "noul": 0.9}))
    assert routed["status"] == "routed" and routed["route_reason"] == "arithmetic_required"
    contradicted = dict(clean, g_support={"type": "choice", "choice": "contradicted", "probabilities": {"direct": 0.02, "partial": 0.02, "insufficient": 0.01, "contradicted": 0.95}})
    assert rules.grounding_signal("X", contradicted)["cap"] == "low"
    address = {"type": "choice", "choice": "address_only", "probabilities": {"legal_fact": 0.05, "address_only": 0.9, "not_applicable": 0.05}}
    legal = dict(clean, g_legal=address, g_legal_rev=address)
    assert "address_as_legal_domicile" in [f["reason"] for f in rules.grounding_signal("Acme AG is domiciled in Zug.", legal)["flags"]]
    assert not rules.grounding_signal("Acme AG is headquartered in Zug.", legal)["flags"], "legal flag needs legal wording in the claim"
    weak_rev = dict(address, probabilities={"legal_fact": 0.45, "address_only": 0.5, "not_applicable": 0.05})
    assert not rules.grounding_signal("Acme AG is domiciled in Zug.", dict(clean, g_legal=address, g_legal_rev=weak_rev))["flags"], \
        "both option orders must clear the threshold"
    report_bank = questions.ALL["report"]
    clean_r = {qid: clean_answer(q) for qid, q in report_bank.items()}
    assert rules.report_signal("verified", clean_r)["flags"] == []
    overstated = {"type": "choice", "choice": "overstated", "probabilities": {"faithful": 0.1, "overstated": 0.9, "unrelated": 0.0}}
    debunk = dict(clean_r, r_fidelity=overstated, r_debunk={"type": "noul", "noul": 0.9})
    assert rules.report_signal("false", debunk)["flags"] == [], "a clean debunk of a false finding is faithful"
    assert rules.report_signal("verified", debunk)["flags"], "the debunk exemption applies only to false verdicts"
    disagree = dict(clean_r, r_fidelity_rev=overstated)
    assert [f["reason"] for f in rules.report_signal("verified", disagree)["flags"]] == ["fidelity_inconclusive"]
    prop = {qid: clean_answer(q) for qid, q in questions.ALL["proposition"].items()}
    broader = {"type": "choice", "choice": "broader_or_changed", "probabilities": {"equivalent": 0.1, "narrower": 0.0, "broader_or_changed": 0.9, "different": 0.0}}
    split = rules.proposition_signal(dict(prop, i_prop_rev=broader))
    assert split["result"] == "inconclusive" and split["flags"], "order disagreement is never a clean pass"
    member = {qid: clean_answer(q) for qid, q in questions.ALL["membership"].items()}
    assert rules.membership_signal("mentions", member)["flags"][0]["reason"] == "relation_disagrees"


# ------------------------------------------------------------------ boundary tests
def test_preflight_opt_in_states(tmp: Path) -> None:
    preflight = load("spotlight_preflight", ROOT / "integrations" / "preflight.py")
    base = load("spotlight_preflight_base", ROOT / "integrations" / "_preflight_base.py")
    manifest = json.loads((ROOT / "integrations" / "decisions" / "manifest.json").read_text())
    cfg = tmp / "pf-config.json"
    preflight.CONFIG_PATH = cfg
    cfg.write_text("{}")
    assert preflight.extra_fields(manifest)["opt_in"] == "undecided" and "choice" in preflight.extra_fields(manifest)
    cfg.write_text(json.dumps({"integrations": {"decisions": {"enabled": True, "decided_at": "2026-09-28"}}}))
    assert preflight.extra_fields(manifest)["opt_in"] == "enabled"
    cfg.write_text(json.dumps({"integrations": {"decisions": {"enabled": False, "decided_at": "2026-09-28"}}}))
    declined = preflight.extra_fields(manifest)
    assert declined["opt_in"] == "declined" and declined["status"] == "dismissed"
    assert preflight.extra_fields({"id": "apify", "type": "api"})["opt_in"] == ""

    def exploding_probe(_manifest):
        raise AssertionError("a declined integration must not be probed")

    saved = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        for key in (None, "sk-or-x"):
            if key:
                os.environ["OPENROUTER_API_KEY"] = key
            report = base.build_report(manifest, smoke_fn=exploding_probe, extra_fields=declined)
            assert report["status"] == "dismissed", "declined stays dismissed with or without a key"
    finally:
        os.environ.pop("OPENROUTER_API_KEY", None)
        if saved is not None:
            os.environ["OPENROUTER_API_KEY"] = saved
    assert preflight.smoke_test(manifest) == (True, None), "decision readiness is local-only"


def test_key_resolution_and_check(tmp: Path) -> None:
    env_file = tmp / "keys.env"
    env_file.write_text("# comment\nexport OPENROUTER_API_KEY='sk-or-test'\nOTHER=1\n")
    saved = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        assert signals_cli.resolve_key({"env_file": str(env_file)}, None) == ("sk-or-test", str(env_file))
        os.environ["OPENROUTER_API_KEY"] = "sk-or-env"
        assert signals_cli.resolve_key({"env_file": str(env_file)}, None) == ("sk-or-env", "environment")
    finally:
        os.environ.pop("OPENROUTER_API_KEY", None)
        if saved is not None:
            os.environ["OPENROUTER_API_KEY"] = saved
    case = fresh_case(tmp / "check")
    cfg = tmp / "check-config.json"
    cfg.write_text(json.dumps({"integrations": {"decisions": {"enabled": True, "env_file": str(env_file)}}}))
    out = io.StringIO(); stdout, sys.stdout = sys.stdout, out
    try:
        signals_cli.main([str(case), "--phase", "gate1", "--config", str(cfg), "--check"])
        signals_cli.main([str(case), "--phase", "gate1", "--config", str(cfg), "--check", "--case-opt-in"])
    finally:
        sys.stdout = stdout
    first, second = [json.loads(line) for line in out.getvalue().splitlines()]
    assert not first["ready"] and first["blockers"] == ["case has not opted in"]
    assert second["ready"] and second["key_source"] == str(env_file)
    assert "sk-or" not in out.getvalue(), "the key must never be printed"
    assert not (case / lib.SIGNALS_PATH).exists(), "--check makes no request and writes nothing"


def test_refusals(tmp: Path) -> None:
    case = fresh_case(tmp / "refuse")
    cfg = config(tmp, {"gate1": "advisory"})
    out = io.StringIO(); stdout, sys.stdout = sys.stdout, out
    try:
        signals_cli.main([str(case), "--phase", "gate1", "--config", str(cfg)], provider=provider_with())
        signals_cli.main([str(case), "--phase", "gate1", "--config", str(cfg), "--case-opt-in", "--sensitive"], provider=provider_with())
    finally:
        sys.stdout = stdout
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert not lines[0]["ran"] and "opted in" in lines[0]["reason"]
    assert not lines[1]["ran"] and "sensitive" in lines[1]["reason"]
    assert not (case / lib.SIGNALS_PATH).exists()


def test_backwards_compatible_without_signals(tmp: Path) -> None:
    case = fresh_case(tmp / "legacy")
    result = run_script("finalize-report.py", case)
    assert result.returncode == 0, result.stdout
    assert "decision checks not configured" in result.stdout
    ledger = json.loads((case / "evidence-map.json").read_text())
    assert "data/decision-signals.json" not in ledger["input_sha256"]
    assert all("decision_signal" not in claim for claim in ledger["claims"])


# ------------------------------------------------------------------ gate1 and report
def test_gate1_advisory_and_enforce(tmp: Path) -> None:
    for mode in ("advisory", "enforce"):
        case = fresh_case(tmp / mode)
        draft = json.loads((case / "data" / "report-draft.json").read_text())
        draft["diagrams"] = [{"id": "confidence", "type": "bar", "title": "Confidence", "caption": "Confidence by finding.",
                              "finding_ids": ["F1"], "metric": "confidence_distribution", "scope": "all"}]
        (case / "data" / "report-draft.json").write_text(json.dumps(draft))
        cfg = config(tmp, {"gate1": mode, "report": mode})
        summary = run_phase(case, cfg, "gate1", provider_with({"g_support": "partial", "g_current": 0.1}))
        assert summary["ran"] and summary["items"] == 2
        doc = signals(case)
        f1, f2 = doc["phases"]["gate1"]["findings"]
        assert f1["status"] == "judged" and f1["cap"] == "medium" and "announced_not_current" in [f["reason"] for f in f1["flags"]]
        assert f2["status"] == "routed" and f2["route_reason"] == "evidence_not_located", "unlocated quotes go to the existing process"
        assert set(f1["facets"]) == {"claim_type", "temporal_status", "source_assertion"}
        try:
            from jsonschema import Draft202012Validator
            schema = json.loads((ROOT / "schemas" / "decision-signals.schema.json").read_text())
            errors = list(Draft202012Validator(schema).iter_errors(doc))
            assert not errors, errors[0].message
        except ImportError:
            print("skip decision-signals schema validation (jsonschema unavailable)")
        result = run_script("finalize-report.py", case)
        assert result.returncode == 0, result.stdout + result.stderr
        c1 = ledger_claim(case, "F1")
        assert c1["report_confidence"] == ("medium" if mode == "enforce" else "high"), (mode, c1["report_confidence"])
        assert c1["decision_signal"]["applied_cap"] == ("medium" if mode == "enforce" else None)
        markdown = (case / "findings-report.md").read_text()
        assert "Decision check:" in markdown and "Classification signal, not verification" in markdown
        chart = markdown[markdown.index("xychart-beta"):markdown.index("```", markdown.index("xychart-beta"))]
        f2_confidence = ledger_claim(case, "F2")["report_confidence"]
        expected = {"low": 0, "medium": 0, "high": 0}
        expected[c1["report_confidence"]] += 1
        expected[f2_confidence] += 1
        assert f"bar [{expected['low']}, {expected['medium']}, {expected['high']}]" in chart, "charts use capped confidence"
        first = {name: (case / name).read_bytes() for name in ("report.html", "findings-report.md", "evidence-map.json")}
        assert run_script("finalize-report.py", case).returncode == 0
        assert first == {name: (case / name).read_bytes() for name in first}, "rendering must stay byte-deterministic"
        e1 = next(e for e in json.loads(run_script("ingest-eligibility.py", case).stdout)["findings"] if e["finding_id"] == "F1")
        assert e1["decision_check"]["applied"] is (mode == "enforce")
        assert e1["layer"] == ("lead" if mode == "enforce" else "durable")
        if mode == "enforce":
            fingerprint = f1["input_sha256"]
            doc["overrides"] = [{"target": "finding:F1", "input_sha256": "0" * 64, "reviewer": "ed", "reason": "r", "at": "t"}]
            (case / lib.SIGNALS_PATH).write_text(json.dumps(doc))
            assert run_script("finalize-report.py", case).returncode == 0
            assert ledger_claim(case, "F1")["report_confidence"] == "medium", "an override for another input does not apply"
            doc["overrides"] = [{"target": "finding:F1", "input_sha256": fingerprint, "reviewer": "", "reason": "r", "at": "t"}]
            (case / lib.SIGNALS_PATH).write_text(json.dumps(doc))
            assert run_script("finalize-report.py", case).returncode == 0
            assert ledger_claim(case, "F1")["report_confidence"] == "medium", "an override without a reviewer does not apply"
            doc["overrides"] = [{"target": "finding:F1", "input_sha256": fingerprint, "reviewer": "ed", "reason": "accurate", "at": "t"}]
            (case / lib.SIGNALS_PATH).write_text(json.dumps(doc))
            assert run_script("finalize-report.py", case).returncode == 0
            assert ledger_claim(case, "F1")["report_confidence"] == "high", "a bound override restores the fact-check confidence"


def test_stale_grounding_signal_not_applied(tmp: Path) -> None:
    case = fresh_case(tmp / "stale")
    cfg = config(tmp, {"gate1": "enforce"})
    run_phase(case, cfg, "gate1", provider_with({"g_support": "contradicted"}))
    source = case / "research" / "official-record.md"
    source.write_text(source.read_text() + "\nUpdated: the record now also names a second officer.\n")
    bundle = json.loads((case / "data" / "evidence-bundle.json").read_text())
    bundle["items"][0]["sha256"] = render_check.sha(source)  # keep the acquisition record consistent
    (case / "data" / "evidence-bundle.json").write_text(json.dumps(bundle))
    result = run_script("finalize-report.py", case)
    assert result.returncode == 0, result.stdout
    c1 = ledger_claim(case, "F1")
    assert c1["decision_signal"]["status"] == "stale" and c1["report_confidence"] == "high", \
        "a changed stored source makes the grounding signal stale"


def test_report_fidelity_stage(tmp: Path) -> None:
    for mode, expect_pass in (("advisory", True), ("enforce", False)):
        case = fresh_case(tmp / f"prose-{mode}")
        cfg = config(tmp, {"gate1": mode, "report": mode})
        run_phase(case, cfg, "report", provider_with({"r_fidelity": "overstated", "r_type": "certainty"}))
        stage = run_script("check-report-fidelity.py", case)
        assert (stage.returncode == 0) is expect_pass, stage.stdout
        final = run_script("finalize-report.py", case)
        assert (final.returncode == 0) is expect_pass, final.stdout
        if expect_pass:
            continue
        assert "report_fidelity" in final.stdout and "treatment:F1.headline" in stage.stdout
        doc = signals(case)
        doc["overrides"] = [{"target": item["target"], "input_sha256": item["input_sha256"], "reviewer": "editor",
                             "reason": "accurate", "at": "2026-09-28"} for item in doc["phases"]["report"]["items"]]
        (case / lib.SIGNALS_PATH).write_text(json.dumps(doc))
        assert run_script("check-report-fidelity.py", case).returncode == 0, "bound reviewer overrides clear the gate"
        draft = json.loads((case / "data" / "report-draft.json").read_text())
        draft["deck"] = draft["deck"] + " Revised."
        (case / "data" / "report-draft.json").write_text(json.dumps(draft))
        check = run_script("check-report-fidelity.py", case)
        assert check.returncode == 1 and "deck" in check.stdout, "edited prose is stale and its old override lapses"
        # A verdict change alone must also make the prose check stale.
        case2 = fresh_case(tmp / "prose-verdict")
        run_phase(case2, cfg, "report", provider_with())
        assert run_script("check-report-fidelity.py", case2).returncode == 0
        fact = json.loads((case2 / "data" / "fact-check.json").read_text())
        fact["claims"][0]["confidence"] = "low"
        (case2 / "data" / "fact-check.json").write_text(json.dumps(fact))
        check = run_script("check-report-fidelity.py", case2)
        assert check.returncode == 1 and "changed after the decision check" in check.stdout


def test_invalid_and_unsafe_signal_files(tmp: Path) -> None:
    case = fresh_case(tmp / "invalid")
    (case / lib.SIGNALS_PATH).write_text("{not json")
    for script in ("check-report-fidelity.py", "finalize-report.py", "ingest-eligibility.py"):
        assert run_script(script, case).returncode != 0, f"{script} must fail loudly on a corrupt signals file"
    outside = tmp / "outside.json"
    outside.write_text("{}")
    (case / lib.SIGNALS_PATH).unlink()
    (case / lib.SIGNALS_PATH).symlink_to(outside)
    try:
        lib.load_signals(case)
        raise AssertionError("a symlinked signals file must be refused")
    except lib.SignalsError:
        pass
    out = io.StringIO(); stdout, sys.stdout = sys.stdout, out
    try:
        code = signals_cli.main([str(case), "--phase", "gate1", "--config", str(config(tmp, {})), "--case-opt-in"], provider=provider_with())
    finally:
        sys.stdout = stdout
    assert code == 2 and outside.read_text() == "{}", "a symlinked signals file is never written through"


def test_symlinked_research_is_not_read(tmp: Path) -> None:
    case = fresh_case(tmp / "symlink")
    outside = tmp / "secret-notes.md"
    outside.write_text("# Private\nThe official record names Ada Lovelace as President of a secret body.\n")
    (case / "research" / "official-record.md").write_text("Nothing relevant here.\n")
    (case / "research" / "link.md").symlink_to(outside)
    findings = json.loads((case / "data" / "findings.json").read_text())
    sources, location = lib.grounding_sources(case, findings["findings"][0])
    assert location == "not_found" and not sources, "symlinks leading outside the case are never read"


def test_malformed_answer_keeps_other_results(tmp: Path) -> None:
    case = fresh_case(tmp / "malformed")
    cfg = config(tmp, {"report": "advisory"})
    summary = run_phase(case, cfg, "report", provider_with(raw={"r_type": {"type": "choice", "choice": "nonsense", "probabilities": {}}}))
    assert summary["ran"] and summary["unavailable"] == summary["items"] and summary["items"] > 0
    phase = signals(case)["phases"]["report"]
    assert phase["status"] == "unavailable" and all(i["status"] == "unavailable" for i in phase["items"])
    stage = run_script("check-report-fidelity.py", case)
    assert stage.returncode == 0 and "unavailable (not judged)" in stage.stdout, "outages are visible, not a clean pass"


def test_ingest_phase(tmp: Path) -> None:
    case = fresh_case(tmp / "ingest")
    shutil.copy(ROOT / "tests" / "fixtures" / "knowledge-batch.sample.json", case / "data" / "knowledge-batch.json")
    batch = json.loads((case / "data" / "knowledge-batch.json").read_text())
    for claim in batch["claims"]:
        claim["origin"]["finding_id"] = "F1"
    (case / "data" / "knowledge-batch.json").write_text(json.dumps(batch))
    cfg = config(tmp, {"gate1": "advisory", "report": "advisory", "ingest": "advisory"})
    assert run_script("finalize-report.py", case).returncode == 0
    run_phase(case, cfg, "gate1", provider_with())
    assert run_script("finalize-report.py", case).returncode == 0
    report_inputs = json.loads((case / "evidence-map.json").read_text())["input_sha256"]
    entities = case / "data" / "ingest-entities.json"
    entities.write_text(json.dumps([{"name": "Morgan", "context": "Morgan approved the payment.", "type": "person"}]))
    existing = case / "data" / "ingest-existing-claims.json"
    existing.write_text(json.dumps([{"id": "old-1", "claim": "Ada Lovelace is President of Northwind Research Cooperative."}]))
    provider = provider_with({"i_prop": "broader_or_changed", "i_member": "mentions", "i_entity_ambiguous": 0.8})
    run_phase(case, cfg, "ingest", provider, ["--entities", str(entities)])
    run_phase(case, cfg, "ingest", provider, ["--existing-claims", str(existing)])
    phase = signals(case, lib.INGEST_SIGNALS_NAME)["phases"]["ingest"]
    assert phase["propositions"][0]["flags"][0]["reason"] == "proposition_broader_or_changed"
    assert phase["memberships"][0]["declared"] == "supports" and phase["memberships"][0]["flags"]
    assert phase["entities"][0]["result"] == "unclear", "an earlier entity run survives a later matching run"
    assert phase["matches"][0]["result"] == "same_claim"
    assert set(phase["groups_generated_at"]) == {"propositions", "memberships", "entities", "matches"}
    validate = run_script("validate-report.py", case)
    assert validate.returncode == 0, "ingest checks must not invalidate the finalized report: " + validate.stdout
    assert json.loads((case / "evidence-map.json").read_text())["input_sha256"] == report_inputs
    checks = json.loads(run_script("ingest-eligibility.py", case).stdout)["ingest_checks"]
    assert {c["status"] for c in checks if c["group"] in ("propositions", "memberships")} == {"judged"}
    assert {c["group"] for c in checks if c["status"] == "inputs_not_supplied"} == {"entities", "matches"}, \
        "stored entity/match results are never presented as current without their inputs"
    batch["claims"][0]["proposition"] = "Acme paid Doe twice."
    (case / "data" / "knowledge-batch.json").write_text(json.dumps(batch))
    checks = json.loads(run_script("ingest-eligibility.py", case).stdout)["ingest_checks"]
    assert any(c["group"] == "propositions" and c["status"] == "stale" for c in checks), "batch edits make ingest checks stale"


def test_missing_grounding_cap_warns(tmp: Path) -> None:
    case = fresh_case(tmp / "nocap")
    records = json.loads(run_script("ingest-eligibility.py", case).stdout)["findings"]
    f1 = next(r for r in records if r["finding_id"] == "F1")
    assert f1["confidence_cap"] is None and f1["warnings"], "a missing grounding cap is visible, not silent"


def test_round_two_regressions(tmp: Path) -> None:
    # Multi-finding prose: changing a non-minimum confidence must make the check stale.
    case = fresh_case(tmp / "multi")
    draft = json.loads((case / "data" / "report-draft.json").read_text())
    draft["framing_finding_ids"] = ["F1", "F2"]
    (case / "data" / "report-draft.json").write_text(json.dumps(draft))
    cfg = config(tmp, {"gate1": "enforce", "report": "enforce"}, "r2.json")
    run_phase(case, cfg, "report", provider_with())
    assert run_script("check-report-fidelity.py", case).returncode == 0
    fact = json.loads((case / "data" / "fact-check.json").read_text())
    fact["claims"][0]["confidence"] = "medium"  # F1 high -> medium; F2 stays low (the minimum)
    (case / "data" / "fact-check.json").write_text(json.dumps(fact))
    check = run_script("check-report-fidelity.py", case)
    assert check.returncode == 1 and "deck" in check.stdout, "every cited finding's confidence is fingerprinted"

    # Ingest-only run never creates the main signals file; a finalized report stays valid.
    case = fresh_case(tmp / "ingest-only")
    assert run_script("finalize-report.py", case).returncode == 0
    entities = case / "data" / "ingest-entities.json"
    entities.write_text(json.dumps([{"name": "Acme AG", "context": "Acme AG paid the fee.", "type": "company"}]))
    run_phase(case, config(tmp, {"ingest": "advisory"}, "r2i.json"), "ingest", provider_with(), ["--entities", str(entities)])
    assert not (case / lib.SIGNALS_PATH).exists() and (case / lib.INGEST_SIGNALS_PATH).exists()
    assert run_script("validate-report.py", case).returncode == 0
    ok = json.loads(subprocess.run([sys.executable, str(ROOT / "scripts" / "ingest-eligibility.py"), str(case),
                                     "--entities", str(entities)], capture_output=True, text=True).stdout)["ingest_checks"]
    assert [c["status"] for c in ok] == ["judged"]
    entities.write_text(json.dumps([{"name": "Acme AG", "context": "Acme AG, a foundation, paid the fee.", "type": "company"},
                                    {"name": "Doe", "context": "Doe signed.", "type": "person"}]))
    statuses = [c["status"] for c in json.loads(subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ingest-eligibility.py"), str(case), "--entities", str(entities)],
        capture_output=True, text=True).stdout)["ingest_checks"]]
    assert statuses == ["stale", "unchecked"], statuses

    # Structural validation of caps; malformed choices never crash; order disagreement is flagged.
    doc = json.loads((case / lib.INGEST_SIGNALS_PATH).read_text())
    doc["phases"]["gate1"] = {"mode": "enforce", "findings": [{"finding_id": "F1", "input_sha256": "0" * 64, "status": "judged", "flags": [], "cap": []}]}
    try:
        lib.validate_signals(doc)
        raise AssertionError("an invalid cap must be rejected")
    except lib.SignalsError:
        pass
    choice_q = {"type": "choice", "instructions": "?", "criteria": {"a": "", "b": ""}}
    assert client.answer_problem(choice_q, {"choice": [], "probabilities": {"a": 1}}) is not None
    assert client.answer_problem(choice_q, {"choice": "a", "probabilities": {"b": 1}}) is not None
    bank = questions.ALL["grounding"]
    clean = {qid: clean_answer(q) for qid, q in bank.items()}
    direct = {"type": "choice", "choice": "direct", "probabilities": {"direct": 1.0, "partial": 0, "insufficient": 0, "contradicted": 0}}
    contra = {"type": "choice", "choice": "contradicted", "probabilities": {"direct": 0, "partial": 0, "insufficient": 0, "contradicted": 1.0}}
    split = rules.grounding_signal("X", dict(clean, g_support=direct, g_support_rev=contra))
    assert split["cap"] == "low" and split["flags"], "either order confidently contradicting is enough"

    # A source edited after rendering fails report validation.
    case = fresh_case(tmp / "dependency")
    run_phase(case, config(tmp, {"gate1": "advisory"}, "r2d.json"), "gate1", provider_with({"g_support": "partial"}))
    assert run_script("finalize-report.py", case).returncode == 0
    source = case / "research" / "official-record.md"
    source.write_text(source.read_text() + "\nAmended later.\n")
    bundle = json.loads((case / "data" / "evidence-bundle.json").read_text())
    bundle["items"][0]["sha256"] = render_check.sha(source)
    (case / "data" / "evidence-bundle.json").write_text(json.dumps(bundle))
    validate = run_script("validate-report.py", case)
    assert validate.returncode != 0 and "changed since rendering" in validate.stdout, validate.stdout

    # Ingest results left in the main file by an earlier layout are reported, not silently dropped.
    main = json.loads((case / lib.SIGNALS_PATH).read_text())
    main["phases"]["ingest"] = {"mode": "advisory", "entities": []}
    (case / lib.SIGNALS_PATH).write_text(json.dumps(main))
    assert any(c["status"] == "legacy_location" for c in lib.ingest_status(case))


def test_round_three_regressions(tmp: Path) -> None:
    # A malformed consent record is not consent.
    assert not lib.valid_opt_in({}) and not lib.valid_opt_in({"by": "", "at": "t"}) and lib.valid_opt_in({"by": "user", "at": "t"})
    case = fresh_case(tmp / "consent")
    (case / lib.INGEST_SIGNALS_PATH).write_text(json.dumps({**signals_cli.empty_document("m"), "opt_in": {}}))
    provider = provider_with()
    out = io.StringIO(); stdout, sys.stdout = sys.stdout, out
    try:
        code = signals_cli.main([str(case), "--phase", "ingest", "--config", str(config(tmp, {}, "r3c.json"))], provider=provider)
    finally:
        sys.stdout = stdout
    assert code == 2 and not provider.calls, "a malformed consent record never authorizes a request"

    # A symlinked knowledge batch is never read or sent.
    case = fresh_case(tmp / "batch-link")
    other = tmp / "other-case-batch.json"
    other.write_text(json.dumps({"claims": [{"id": "c", "version": 1, "proposition": "Secret proposition.", "origin": {"finding_id": "F1"}}]}))
    (case / "data" / "knowledge-batch.json").symlink_to(other)
    provider = provider_with()
    out = io.StringIO(); stdout, sys.stdout = sys.stdout, out
    try:
        code = signals_cli.main([str(case), "--phase", "ingest", "--config", str(config(tmp, {}, "r3b.json")), "--case-opt-in"], provider=provider)
    finally:
        sys.stdout = stdout
    assert code == 2 and not provider.calls, "symlinked case inputs are refused before any request"

    # Versioned batch records resolve exactly; unresolved endpoints are routed, never judged.
    case = fresh_case(tmp / "versions")
    batch = json.loads((ROOT / "tests" / "fixtures" / "knowledge-batch.sample.json").read_text())
    claim_v1 = batch["claims"][0]
    claim_v1["origin"]["finding_id"] = "F1"
    claim_v2 = {**claim_v1, "version": 2, "proposition": "Acme paid Doe twice."}
    batch["claims"].append(claim_v2)
    dangling = {**batch["claim_event_memberships"][0], "id": "relation:dangling", "claim": {"id": claim_v1["id"], "version": 9}}
    batch["claim_event_memberships"].append(dangling)
    (case / "data" / "knowledge-batch.json").write_text(json.dumps(batch))
    targets = {i["target"]: i for i in lib.batch_items(case)}
    assert f"claim:{claim_v1['id']}@1" in targets and f"claim:{claim_v1['id']}@2" in targets
    member = next(i for t, i in targets.items() if t.startswith("membership:relation:claim-event"))
    assert member["state"]["claim"] == claim_v1["proposition"], "a membership is judged against its exact claim version"
    assert next(i for t, i in targets.items() if t.startswith("membership:relation:dangling"))["unresolved"]
    run_phase(case, config(tmp, {"ingest": "advisory"}, "r3v.json"), "ingest", provider_with())
    phase = signals(case, lib.INGEST_SIGNALS_NAME)["phases"]["ingest"]
    routed = [m for m in phase["memberships"] if m["target"].startswith("membership:relation:dangling")]
    assert routed and routed[0]["status"] == "routed" and routed[0]["result"] == "unresolved"
    statuses = {c["target"]: c["status"] for c in lib.ingest_status(case)}
    assert statuses[routed[0]["target"]] == "unresolved"


def test_input_containment(tmp: Path) -> None:
    case = fresh_case(tmp / "inputs")
    outside = tmp / "other-case-entities.json"
    outside.write_text(json.dumps([{"name": "Secret Person", "context": "Secret Person met the minister.", "type": "person"}]))
    cfg = config(tmp, {"ingest": "advisory"}, "contain.json")
    link = case / "data" / "ingest-entities.json"
    link.symlink_to(outside)
    for value in (outside, link):
        provider = provider_with()
        out = io.StringIO(); stdout, sys.stdout = sys.stdout, out
        try:
            code = signals_cli.main([str(case), "--phase", "ingest", "--config", str(cfg), "--case-opt-in", "--entities", str(value)], provider=provider)
        finally:
            sys.stdout = stdout
        assert code == 2 and not provider.calls, f"{value} must be refused before any request"
    for bad in ("../findings.json", "data/../x.json"):
        try:
            lib.read_case_file(case, bad)
            raise AssertionError("escaping relative paths must be refused")
        except lib.SignalsError:
            pass
    assert lib.read_case_file(case, "data/absent.json") is None


def test_round_five_regressions(tmp: Path) -> None:
    # A swapped case pathname cannot redirect reads made under a pinned case.
    case_a = fresh_case(tmp / "pin-a")
    case_b = fresh_case(tmp / "pin-b")
    findings_b = json.loads((case_b / "data" / "findings.json").read_text())
    findings_b["findings"][0]["claim"] = "Case B secret claim."
    (case_b / "data" / "findings.json").write_text(json.dumps(findings_b))
    with lib.pinned_case(case_a) as pinned:
        moved = case_a.parent / "case-moved"
        os.rename(pinned, moved)
        os.symlink(case_b.resolve(), pinned)
        try:
            claims = [f["claim"] for f in lib.case_findings(pinned)]
        finally:
            os.unlink(pinned)
            os.rename(moved, pinned)
    assert "Case B secret claim." not in claims, "pinned reads stay in the authorized case"

    # JSON null is invalid, never "absent".
    case = fresh_case(tmp / "null")
    (case / lib.SIGNALS_PATH).write_text("null")
    try:
        lib.load_signals(case)
        raise AssertionError("a null signals document must be rejected")
    except lib.SignalsError:
        pass
    assert run_script("check-report-fidelity.py", case).returncode != 0

    # A missing explicit input stops the run before any request and keeps earlier results.
    case = fresh_case(tmp / "missing-input")
    entities = case / "data" / "ingest-entities.json"
    entities.write_text(json.dumps([{"name": "Acme AG", "context": "Acme AG paid.", "type": "company"}]))
    cfg = config(tmp, {"ingest": "advisory"}, "r5.json")
    provider = provider_with()
    out = io.StringIO(); stdout, sys.stdout = sys.stdout, out
    try:
        code = signals_cli.main([str(case), "--phase", "ingest", "--config", str(cfg), "--case-opt-in", "--entities", str(entities),
                                 "--existing-claims", str(case / "data" / "ingest-existing-claims.json")], provider=provider)
    finally:
        sys.stdout = stdout
    assert code == 2 and not provider.calls and not (case / lib.INGEST_SIGNALS_PATH).exists(), \
        "a missing explicit input is an error, never an empty list"

    # Without descriptor-anchored reads the networked step refuses and preflight says why.
    saved = lib.ANCHORED_READS
    lib.ANCHORED_READS = False
    try:
        out = io.StringIO(); stdout, sys.stdout = sys.stdout, out
        try:
            signals_cli.main([str(case), "--phase", "gate1", "--config", str(cfg), "--case-opt-in"], provider=provider_with())
            signals_cli.main([str(case), "--phase", "gate1", "--config", str(cfg), "--check"])
        finally:
            sys.stdout = stdout
        refused, check = [json.loads(line) for line in out.getvalue().splitlines()]
        assert not refused["ran"] and "anchored" in refused["reason"] and not check["ready"]
    finally:
        lib.ANCHORED_READS = saved
    preflight = load("spotlight_preflight_r5", ROOT / "integrations" / "preflight.py")
    manifest = json.loads((ROOT / "integrations" / "decisions" / "manifest.json").read_text())
    saved_dir_fd = os.supports_dir_fd
    os.supports_dir_fd = set()
    try:
        fields = preflight.extra_fields(manifest)
    finally:
        os.supports_dir_fd = saved_dir_fd
    assert fields["opt_in"] == "unavailable" and fields["status"] == "dismissed"


def main() -> int:
    for test in (test_client_boundary, test_rules):
        test()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        for test in (test_preflight_opt_in_states, test_key_resolution_and_check, test_refusals,
                     test_backwards_compatible_without_signals, test_gate1_advisory_and_enforce,
                     test_stale_grounding_signal_not_applied, test_report_fidelity_stage,
                     test_invalid_and_unsafe_signal_files, test_symlinked_research_is_not_read,
                     test_malformed_answer_keeps_other_results, test_ingest_phase, test_missing_grounding_cap_warns,
                     test_round_two_regressions, test_round_three_regressions, test_input_containment,
                     test_round_five_regressions):
            test(tmp)
    print("decision-signals-check: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
