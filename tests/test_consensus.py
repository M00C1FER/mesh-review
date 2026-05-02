"""Tests for consensus + falsification gate."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from mesh_review.review.core import (
    Finding, ReviewResult, ReviewConfig, run_review,
    _parse_findings, _normalize_title,
    _validate_finding_item,
)
from mesh_review.review.consensus import build_consensus
from mesh_review.review.falsify import (
    sigma_gate, render_pr_comments,
    make_subprocess_falsifier, _parse_falsifier_output,
    to_json,
)


def f(cli, file="x.py", line=10, sev="high", title="t", body="b"):
    return Finding(cli=cli, file=file, line=line, severity=sev, title=title, body=body)


def test_unanimous_clusters():
    r = [
        ReviewResult(cli="claude",  findings=[f("claude")],  raw_output=""),
        ReviewResult(cli="gemini",  findings=[f("gemini")],  raw_output=""),
        ReviewResult(cli="copilot", findings=[f("copilot")], raw_output=""),
    ]
    consensus = build_consensus(r)
    assert len(consensus) == 1
    assert consensus[0].agreement == "unanimous"
    assert set(consensus[0].clis) == {"claude", "gemini", "copilot"}


def test_solo_finding():
    r = [
        ReviewResult(cli="claude",  findings=[f("claude", line=10)], raw_output=""),
        ReviewResult(cli="gemini",  findings=[],  raw_output=""),
        ReviewResult(cli="copilot", findings=[], raw_output=""),
    ]
    consensus = build_consensus(r)
    assert len(consensus) == 1
    assert consensus[0].agreement == "solo"


def test_severity_takes_worst():
    r = [
        ReviewResult(cli="claude",  findings=[f("claude", sev="high")],   raw_output=""),
        ReviewResult(cli="gemini",  findings=[f("gemini", sev="critical")], raw_output=""),
    ]
    consensus = build_consensus(r)
    assert consensus[0].severity == "critical"


def test_sigma_gate_no_falsification_survives():
    r = [
        ReviewResult(cli="claude",  findings=[f("claude")],  raw_output=""),
        ReviewResult(cli="gemini",  findings=[f("gemini")],  raw_output=""),
    ]
    consensus = build_consensus(r)
    gate = sigma_gate(consensus)  # default falsifier no-ops
    assert gate[0]["survived"] is True


def test_sigma_gate_falsification_drops_finding():
    r = [
        ReviewResult(cli="claude", findings=[f("claude")], raw_output=""),
        ReviewResult(cli="gemini", findings=[f("gemini")], raw_output=""),
    ]
    consensus = build_consensus(r)

    def aggressive(cli, prompt):
        return {"falsified": True, "confidence": 0.95, "rationale": f"{cli}: false positive"}

    gate = sigma_gate(consensus, falsifier=aggressive)
    assert gate[0]["survived"] is False


def test_pr_comments_only_for_survivors():
    r = [
        ReviewResult(cli="claude",  findings=[f("claude")],  raw_output=""),
        ReviewResult(cli="gemini",  findings=[f("gemini")],  raw_output=""),
        ReviewResult(cli="copilot", findings=[f("copilot")], raw_output=""),
    ]
    consensus = build_consensus(r)
    gate = sigma_gate(consensus)
    comments = render_pr_comments(gate)
    assert len(comments) == 1
    assert comments[0]["path"] == "x.py"
    assert "unanimous" in comments[0]["body"]


# ── fingerprint clustering — title-aware, ±line tolerant ────────────────


def test_fingerprint_clusters_rephrasings():
    """Titles like `JSON parsing brittle` and `Brittle JSON parsing` cluster."""
    a = f("claude", title="JSON parsing brittle")
    b = f("gemini", title="Brittle JSON parsing")
    assert a.fingerprint() == b.fingerprint()


def test_fingerprint_separates_distinct_findings():
    """Different titles in the same file at the same line still don't cluster."""
    a = f("claude", title="SQL injection in login")
    b = f("gemini", title="Race condition on cache")
    assert a.fingerprint() != b.fingerprint()


def test_fingerprint_line_window_is_tight():
    """Lines 10 and 11 cluster (same ±2 window); lines 10 and 30 do not."""
    near_a = f("claude", line=10, title="same issue")
    near_b = f("gemini", line=11, title="same issue")
    far    = f("copilot", line=30, title="same issue")
    assert near_a.fingerprint() == near_b.fingerprint()
    assert near_a.fingerprint() != far.fingerprint()


def test_normalize_title_strips_stopwords_and_punctuation():
    assert _normalize_title("Brittle JSON parsing") == _normalize_title("JSON parsing — brittle!")


# ── _parse_findings — JSON tolerance ─────────────────────────────────────


def test_parse_findings_direct_json():
    out = _parse_findings('[{"cli":"a","file":"x.py","line":1,"severity":"high","title":"t","body":"b"}]',
                          default_cli="a", default_file="x.py")
    assert len(out) == 1 and out[0].title == "t"


def test_parse_findings_fenced_json():
    """Markdown-fenced ```json ... ``` blocks must parse."""
    out = _parse_findings(
        'Sure, here is my review:\n```json\n[{"cli":"a","file":"x.py","line":2,"severity":"low","title":"t","body":"b"}]\n```\n',
        default_cli="a", default_file="x.py",
    )
    assert len(out) == 1 and out[0].line == 2


def test_parse_findings_handles_brackets_in_prose():
    """Prior `text.find('[') ... text.rfind(']')` broke when prose contained `[CRITICAL]`.
    Balanced-bracket scan picks the longest valid array."""
    out = _parse_findings(
        'I found a [CRITICAL] issue. Here it is: '
        '[{"cli":"a","file":"x.py","line":3,"severity":"critical","title":"t","body":"b"}]'
        ' Hope that helps [end].',
        default_cli="a", default_file="x.py",
    )
    assert len(out) == 1 and out[0].severity == "critical"


def test_parse_findings_empty_input():
    assert _parse_findings("", default_cli="a", default_file="x.py") == []
    assert _parse_findings("nothing parseable here", default_cli="a", default_file="x.py") == []


# ── falsifier output parsing ─────────────────────────────────────────────


def test_parse_falsifier_output_direct():
    obj = _parse_falsifier_output('{"falsified": true, "confidence": 0.9, "rationale": "x"}')
    assert obj == {"falsified": True, "confidence": 0.9, "rationale": "x"}


def test_parse_falsifier_output_fenced():
    obj = _parse_falsifier_output(
        'Verdict:\n```json\n{"falsified": false, "confidence": 0.2, "rationale": "y"}\n```\n'
    )
    assert obj is not None and obj["falsified"] is False


def test_parse_falsifier_output_unparseable():
    assert _parse_falsifier_output("just prose, no JSON here") is None


def test_parse_falsifier_output_handles_nested_objects():
    """Rationales sometimes embed JSON snippets — must parse the outer object."""
    raw = (
        'Verdict: \n'
        '{"falsified": false, "confidence": 0.7, "rationale": "ok", '
        '"meta": {"model": "claude-3", "score": 0.85}}'
    )
    obj = _parse_falsifier_output(raw)
    assert obj is not None and obj["falsified"] is False
    assert obj["meta"]["model"] == "claude-3"


def test_parse_falsifier_output_handles_braces_in_strings():
    """The regex `\\{[^{}]*\\}` would mis-truncate at `{` in a string. Must not."""
    raw = '{"falsified": true, "confidence": 0.9, "rationale": "string with { brace"}'
    obj = _parse_falsifier_output(raw)
    assert obj is not None and obj["falsified"] is True
    assert "{ brace" in obj["rationale"]


def test_make_subprocess_falsifier_handles_missing_binary():
    cfgs = [ReviewConfig(cli="claude", cmd=["claude-does-not-exist"], timeout_s=5)]
    falsifier = make_subprocess_falsifier(cfgs)
    result = falsifier("claude", "irrelevant prompt")
    assert result["falsified"] is False
    assert result["confidence"] == 0.0
    assert "not on PATH" in result["rationale"]


def test_make_subprocess_falsifier_unregistered_cli():
    falsifier = make_subprocess_falsifier(configs=[])
    result = falsifier("unknown-cli", "prompt")
    assert result["falsified"] is False
    assert "not registered" in result["rationale"]


# ── original test (preserved) ─────────────────────────────────────────────


def test_run_review_with_runners(tmp_path):
    """End-to-end with in-process runners (no shell-out)."""
    src = tmp_path / "demo.py"
    src.write_text("def foo():\n    pass\n")

    def fake_runner_factory(cli, finds):
        def run(path, prompt):
            return ReviewResult(cli=cli, findings=finds, raw_output="")
        return run

    cfgs = [
        ReviewConfig(cli="claude",  runner=fake_runner_factory("claude",  [f("claude",  file=str(src))])),
        ReviewConfig(cli="gemini",  runner=fake_runner_factory("gemini",  [f("gemini",  file=str(src))])),
        ReviewConfig(cli="copilot", runner=fake_runner_factory("copilot", [f("copilot", file=str(src))])),
    ]
    results = run_review(str(src), configs=cfgs)
    assert len(results) == 3
    consensus = build_consensus(results)
    assert consensus[0].agreement == "unanimous"


# ── sigma_gate edge cases ─────────────────────────────────────────────────


def test_sigma_gate_all_falsifiers_timeout_keeps_finding():
    """When all falsifiers return timeout (falsified=False), the finding survives."""
    r = [
        ReviewResult(cli="claude", findings=[f("claude")], raw_output=""),
        ReviewResult(cli="gemini", findings=[f("gemini")], raw_output=""),
    ]
    consensus = build_consensus(r)

    def timeout_falsifier(cli, prompt):
        return {"falsified": False, "confidence": 0.0, "rationale": f"{cli}: timeout"}

    gate = sigma_gate(consensus, falsifier=timeout_falsifier)
    assert gate[0]["survived"] is True


def test_sigma_gate_falsifier_raises_exception_keeps_finding():
    """If the falsifier callable raises, the finding is kept (safe default)."""
    r = [ReviewResult(cli="claude", findings=[f("claude")], raw_output="")]
    consensus = build_consensus(r)

    def exploding_falsifier(cli, prompt):
        raise RuntimeError("network error")

    gate = sigma_gate(consensus, falsifier=exploding_falsifier)
    assert gate[0]["survived"] is True
    # The error is recorded in the falsifications list
    assert "err:" in gate[0]["falsifications"][0]["rationale"]


def test_sigma_gate_confidence_below_threshold_keeps_finding():
    """confidence just below threshold (default 0.7) must NOT drop the finding."""
    r = [ReviewResult(cli="claude", findings=[f("claude")], raw_output="")]
    consensus = build_consensus(r)

    def borderline_falsifier(cli, prompt):
        return {"falsified": True, "confidence": 0.69, "rationale": "marginal"}

    gate = sigma_gate(consensus, falsifier=borderline_falsifier)
    assert gate[0]["survived"] is True


def test_sigma_gate_confidence_at_threshold_drops_finding():
    """confidence exactly at threshold drops the finding."""
    r = [ReviewResult(cli="claude", findings=[f("claude")], raw_output="")]
    consensus = build_consensus(r)

    def threshold_falsifier(cli, prompt):
        return {"falsified": True, "confidence": 0.7, "rationale": "exact threshold"}

    gate = sigma_gate(consensus, falsifier=threshold_falsifier)
    assert gate[0]["survived"] is False


# ── confidence bounds clamping ────────────────────────────────────────────


def test_make_subprocess_falsifier_clamps_confidence_above_one():
    """confidence > 1.0 returned by the LLM is clamped to 1.0."""
    cfgs = [ReviewConfig(cli="test-cli", cmd=["test-cli-bin"], timeout_s=5)]
    falsifier = make_subprocess_falsifier(cfgs)
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = '{"falsified": true, "confidence": 1.5, "rationale": "x"}'
    with patch("shutil.which", return_value="/usr/bin/test-cli-bin"), \
         patch("subprocess.run", return_value=mock_proc):
        result = falsifier("test-cli", "prompt")
    assert result["confidence"] == 1.0
    assert result["falsified"] is True


def test_make_subprocess_falsifier_clamps_confidence_below_zero():
    """confidence < 0.0 returned by the LLM is clamped to 0.0."""
    cfgs = [ReviewConfig(cli="test-cli", cmd=["test-cli-bin"], timeout_s=5)]
    falsifier = make_subprocess_falsifier(cfgs)
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = '{"falsified": false, "confidence": -0.3, "rationale": "y"}'
    with patch("shutil.which", return_value="/usr/bin/test-cli-bin"), \
         patch("subprocess.run", return_value=mock_proc):
        result = falsifier("test-cli", "prompt")
    assert result["confidence"] == 0.0


# ── make_subprocess_falsifier subprocess paths ────────────────────────────


def test_make_subprocess_falsifier_timeout():
    """subprocess.TimeoutExpired is caught and returns a non-falsifying result."""
    cfgs = [ReviewConfig(cli="slow-cli", cmd=["slow-cli-bin"], timeout_s=1)]
    falsifier = make_subprocess_falsifier(cfgs)
    with patch("shutil.which", return_value="/usr/bin/slow-cli-bin"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="slow-cli-bin", timeout=1)):
        result = falsifier("slow-cli", "prompt")
    assert result["falsified"] is False
    assert result["confidence"] == 0.0
    assert "timeout" in result["rationale"]


def test_make_subprocess_falsifier_nonzero_exit():
    """Non-zero returncode is caught and returns a non-falsifying result."""
    cfgs = [ReviewConfig(cli="failing-cli", cmd=["failing-cli-bin"], timeout_s=5)]
    falsifier = make_subprocess_falsifier(cfgs)
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stderr = "something went wrong"
    with patch("shutil.which", return_value="/usr/bin/failing-cli-bin"), \
         patch("subprocess.run", return_value=mock_proc):
        result = falsifier("failing-cli", "prompt")
    assert result["falsified"] is False
    assert "exited 1" in result["rationale"]


def test_make_subprocess_falsifier_unparseable_output():
    """Unparseable stdout is handled gracefully as a non-falsifying result."""
    cfgs = [ReviewConfig(cli="noisy-cli", cmd=["noisy-cli-bin"], timeout_s=5)]
    falsifier = make_subprocess_falsifier(cfgs)
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "This is just prose with no JSON at all."
    with patch("shutil.which", return_value="/usr/bin/noisy-cli-bin"), \
         patch("subprocess.run", return_value=mock_proc):
        result = falsifier("noisy-cli", "prompt")
    assert result["falsified"] is False
    assert "unparseable" in result["rationale"]


def test_make_subprocess_falsifier_valid_json_output():
    """Valid JSON output is parsed and returned correctly."""
    cfgs = [ReviewConfig(cli="good-cli", cmd=["good-cli-bin"], timeout_s=5)]
    falsifier = make_subprocess_falsifier(cfgs)
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = '{"falsified": true, "confidence": 0.9, "rationale": "clear false positive"}'
    with patch("shutil.which", return_value="/usr/bin/good-cli-bin"), \
         patch("subprocess.run", return_value=mock_proc):
        result = falsifier("good-cli", "prompt")
    assert result["falsified"] is True
    assert result["confidence"] == 0.9
    assert "false positive" in result["rationale"]


# ── render_pr_comments filtering ─────────────────────────────────────────


def test_render_pr_comments_skips_solo_findings():
    """render_pr_comments only emits majority/unanimous findings, not solo ones."""
    r = [
        ReviewResult(cli="claude", findings=[f("claude")], raw_output=""),
    ]
    consensus = build_consensus(r)
    assert consensus[0].agreement == "solo"
    gate = sigma_gate(consensus)  # default falsifier → survives
    comments = render_pr_comments(gate)
    assert comments == []


# ── consensus severity edge cases ────────────────────────────────────────


def test_build_consensus_unknown_severity_returns_info():
    """Findings with unrecognised severity fall through to 'info'."""
    finding = Finding(cli="test", file="x.py", line=1, severity="unknown",
                      title="weird finding", body="body")
    res = [ReviewResult(cli="test", findings=[finding], raw_output="")]
    consensus = build_consensus(res)
    assert consensus[0].severity == "info"


def test_severity_mismatch_creates_separate_clusters():
    """Same file+line+title but different severity → two separate clusters.

    Severity is intentionally part of the fingerprint so 'high' and 'critical'
    reports of the same line are not accidentally merged."""
    high_finding = Finding(cli="claude", file="x.py", line=10,
                           severity="high", title="sql injection", body="h")
    crit_finding = Finding(cli="gemini", file="x.py", line=10,
                           severity="critical", title="sql injection", body="c")
    r = [
        ReviewResult(cli="claude", findings=[high_finding], raw_output=""),
        ReviewResult(cli="gemini", findings=[crit_finding], raw_output=""),
    ]
    consensus = build_consensus(r)
    assert len(consensus) == 2
    severities = {c.severity for c in consensus}
    assert severities == {"high", "critical"}


# ── to_json helper ────────────────────────────────────────────────────────


def test_to_json_with_to_dict_object():
    """to_json() calls to_dict() on objects that have it."""
    r = [
        ReviewResult(cli="a", findings=[f("a")], raw_output=""),
        ReviewResult(cli="b", findings=[f("b")], raw_output=""),
    ]
    consensus = build_consensus(r)
    cf = consensus[0]
    result = to_json(cf)
    import json
    parsed = json.loads(result)
    assert "title" in parsed
    assert "agreement" in parsed


def test_to_json_with_plain_dict():
    """to_json() passes through plain dicts unchanged."""
    import json
    result = to_json({"key": "value", "number": 42})
    parsed = json.loads(result)
    assert parsed == {"key": "value", "number": 42}


# ── run_review exception path ─────────────────────────────────────────────


def test_run_review_runner_raises_exception(tmp_path):
    """If a runner raises unexpectedly, the error is captured and other CLIs succeed."""
    src = tmp_path / "demo.py"
    src.write_text("x = 1\n")

    def crashing_runner(path, prompt):
        raise ValueError("unexpected crash")

    def ok_runner(path, prompt):
        return ReviewResult(cli="ok", findings=[f("ok", file=path)], raw_output="")

    cfgs = [
        ReviewConfig(cli="crash", runner=crashing_runner),
        ReviewConfig(cli="ok",    runner=ok_runner),
    ]
    results = run_review(str(src), configs=cfgs)
    assert len(results) == 2
    crash_result = next(r for r in results if r.cli == "crash")
    ok_result    = next(r for r in results if r.cli == "ok")
    assert crash_result.error is not None
    assert "ValueError" in crash_result.error
    assert ok_result.error is None
    assert len(ok_result.findings) == 1


# ── shell runner binary-not-found path ───────────────────────────────────


def test_shell_runner_binary_not_found(tmp_path):
    """shell runner returns error result (not exception) when binary is absent."""
    src = tmp_path / "code.py"
    src.write_text("pass\n")
    cfgs = [ReviewConfig(cli="missing-binary", cmd=["__no_such_binary__"], timeout_s=5)]
    results = run_review(str(src), configs=cfgs)
    assert len(results) == 1
    assert results[0].error is not None
    assert "not found on PATH" in results[0].error



# ── _parse_falsifier_output empty / brace-escape paths ───────────────────


def test_parse_falsifier_output_empty_string_returns_none():
    """Empty input → None (not an exception)."""
    assert _parse_falsifier_output("") is None
    assert _parse_falsifier_output("   ") is None


def test_parse_falsifier_output_balanced_brace_invalid_json():
    """A balanced-brace candidate that isn't valid JSON is skipped gracefully."""
    # {not: valid} has balanced braces but isn't parseable JSON
    raw = "review says: {not: valid json here}"
    result = _parse_falsifier_output(raw)
    assert result is None


def test_balanced_brace_handles_backslash_escape_in_string():
    """Backslash escape sequences inside JSON strings are handled correctly."""
    raw = '{"falsified": false, "confidence": 0.1, "rationale": "path\\\\file"}'
    result = _parse_falsifier_output(raw)
    assert result is not None
    assert result["falsified"] is False


# ── _parse_findings non-dict items ───────────────────────────────────────


def test_parse_findings_skips_non_dict_items():
    """Arrays that mix dicts with non-dict items skip the non-dicts silently."""
    out = _parse_findings(
        '[{"cli":"a","file":"x.py","line":1,"severity":"high","title":"t","body":"b"}, '
        '"just a string", 42, null]',
        default_cli="a", default_file="x.py",
    )
    assert len(out) == 1
    assert out[0].title == "t"


# ── FINDING_SCHEMA and _validate_finding_item ─────────────────────────────


def test_finding_schema_is_exported():
    """FINDING_SCHEMA is a dict with the expected JSON Schema structure."""
    from mesh_review import FINDING_SCHEMA as schema
    assert isinstance(schema, dict)
    assert schema.get("type") == "object"
    assert "properties" in schema
    props = schema["properties"]
    for field in ("file", "severity", "title", "body"):
        assert field in props, f"FINDING_SCHEMA missing property '{field}'"
    assert "enum" in props["severity"]


def test_validate_finding_item_valid():
    """A well-formed finding dict produces no validation errors."""
    item = {"file": "x.py", "line": 10, "severity": "high", "title": "T", "body": "B"}
    assert _validate_finding_item(item) == []


def test_validate_finding_item_missing_required_field():
    """Missing a required field produces an error."""
    item = {"file": "x.py", "severity": "high", "title": "T"}  # missing "body"
    errors = _validate_finding_item(item)
    assert any("body" in e for e in errors)


def test_validate_finding_item_invalid_severity():
    """An unknown severity string is reported as an error."""
    item = {"file": "x.py", "severity": "extreme", "title": "T", "body": "B"}
    errors = _validate_finding_item(item)
    assert any("severity" in e for e in errors)


def test_validate_finding_item_non_dict():
    """A non-dict value is reported as an error."""
    errors = _validate_finding_item("just a string")
    assert errors != []


def test_validate_finding_item_invalid_line_type():
    """A non-integer, non-null line value is reported as an error."""
    item = {"file": "x.py", "severity": "high", "title": "T", "body": "B", "line": "10"}
    errors = _validate_finding_item(item)
    assert any("line" in e for e in errors)


def test_parse_findings_coerces_unknown_severity_to_info():
    """_parse_findings coerces an unknown severity value to 'info'."""
    out = _parse_findings(
        '[{"cli":"a","file":"x.py","line":1,"severity":"EXTREME","title":"t","body":"b"}]',
        default_cli="a", default_file="x.py",
    )
    assert len(out) == 1
    assert out[0].severity == "info"
