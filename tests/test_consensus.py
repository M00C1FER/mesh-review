"""Tests for consensus + falsification gate."""
from __future__ import annotations

from mesh_review.review.core import Finding, ReviewResult, ReviewConfig, run_review
from mesh_review.review.consensus import build_consensus
from mesh_review.review.falsify import sigma_gate, render_pr_comments


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
