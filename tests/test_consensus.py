"""Tests for consensus + falsification gate."""
from __future__ import annotations

from mesh_review.review.core import (
    Finding, ReviewResult, ReviewConfig, run_review,
    _parse_findings, _normalize_title,
)
from mesh_review.review.consensus import build_consensus
from mesh_review.review.falsify import (
    sigma_gate, render_pr_comments,
    make_subprocess_falsifier, _parse_falsifier_output,
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
