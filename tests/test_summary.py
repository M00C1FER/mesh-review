"""Tests for pr-summary-mesh — diff parsing, merge, vote, config."""
from __future__ import annotations

import pytest

from mesh_review.summary import (
    SummaryConfig, SummaryDoc, run_summary,
    merge_structural, vote_best,
    load_config_yaml, parse_inline_summarizer,
)
from mesh_review.summary.core import _parse_summary
from mesh_review.summary.diff import StaticDiffProvider
from mesh_review.summary.merge import render_pr_body


# ── _parse_summary ────────────────────────────────────────────────────────


def test_parse_well_formed_output():
    raw = """TLDR: Refactors the auth flow to use JWTs.
FILES:
- auth/jwt.py: new JWT issuer
- tests/test_auth.py: updated
RISK: Breaks any existing session cookies.
TESTS: Run pytest tests/test_auth.py."""
    doc = _parse_summary("claude", raw)
    assert "Refactors" in doc.tldr
    assert "auth/jwt.py" in doc.files_changed
    assert "session cookies" in doc.risk
    assert "pytest" in doc.test_plan


def test_parse_empty_output():
    doc = _parse_summary("gemini", "")
    assert doc.is_empty()


def test_parse_partial_output():
    raw = "TLDR: thin summary only"
    doc = _parse_summary("copilot", raw)
    assert doc.tldr == "thin summary only"
    assert not doc.files_changed
    assert not doc.risk


def test_parse_markdown_bold_prefixes():
    """LLMs frequently bold prefixes — `**TLDR:**` must still match."""
    raw = """**TLDR:** Refactors auth.
**FILES:**
- a.py: x
**RISK:** none
**TESTS:** pytest"""
    doc = _parse_summary("claude", raw)
    assert "Refactors auth" in doc.tldr
    assert "a.py" in doc.files_changed
    assert "none" in doc.risk
    assert "pytest" in doc.test_plan


def test_parse_underscore_emphasis_prefixes():
    """`_TLDR:_` (italic) also valid markdown — should still match."""
    raw = "_TLDR:_ italic prefix\n_RISK:_ italic risk"
    doc = _parse_summary("gemini", raw)
    assert "italic prefix" in doc.tldr
    assert "italic risk" in doc.risk


# ── merge / vote ──────────────────────────────────────────────────────────


def _doc(cli, **kw):
    return SummaryDoc(cli=cli, **kw)


def test_merge_concatenates_with_attribution():
    """Distinctly-different per-CLI summaries get separate [cli] attribution."""
    docs = [
        _doc("claude", tldr="Refactor auth flow to issuer-based JWT tokens"),
        _doc("gemini", tldr="Drops session cookies; adds CSRF middleware",
             files_changed="- f.py: x"),
    ]
    merged = merge_structural(docs)
    assert "[claude]" in merged.tldr
    assert "[gemini]" in merged.tldr
    assert "[gemini]" in merged.files_changed


def test_merge_dedups_near_duplicate_paragraphs():
    """When 2+ CLIs say nearly the same thing, the merge collapses to one
    cluster with combined attribution (`[claude+gemini]`)."""
    docs = [
        _doc("claude", tldr="Adds JWT auth to the API"),
        _doc("gemini", tldr="Adds JWT auth to the API."),  # identical bar a period
        _doc("copilot", tldr="Refactor pagination to cursor-based"),
    ]
    merged = merge_structural(docs)
    # Claude + Gemini cluster (order-insensitive in attribution)
    assert "[claude+gemini]" in merged.tldr or "[gemini+claude]" in merged.tldr
    # Copilot's distinct paragraph is preserved separately
    assert "[copilot]" in merged.tldr
    assert "cursor-based" in merged.tldr.lower()


def test_merge_handles_all_failed():
    docs = [_doc("claude", error="not on PATH"), _doc("gemini", error="timeout")]
    merged = merge_structural(docs)
    assert merged.error is not None
    assert "claude" in merged.error and "gemini" in merged.error


def test_merge_partial_failure_includes_footnote():
    docs = [
        _doc("claude", tldr="real summary"),
        _doc("gemini", error="timeout"),
    ]
    merged = merge_structural(docs)
    assert "real summary" in merged.tldr
    assert "gemini" in merged.tldr.lower()


def test_vote_picks_most_thorough():
    docs = [
        _doc("a", tldr="x"),
        _doc("b", tldr="x" * 50, files_changed="y" * 50, risk="z"),
        _doc("c", tldr="x" * 30),
    ]
    pick = vote_best(docs)
    assert pick.cli == "b"


def test_vote_drops_errors():
    docs = [_doc("a", error="x"), _doc("b", tldr="ok")]
    pick = vote_best(docs)
    assert pick.cli == "b"


# ── render_pr_body ────────────────────────────────────────────────────────


def test_render_pr_body_includes_marker():
    merged = _doc("merged", tldr="hi", files_changed="- f", risk="rs", test_plan="ts")
    body = render_pr_body(merged)
    assert body.count("<!-- pr-summary-mesh -->") == 2
    assert "TL;DR" in body
    assert "Changed files" in body
    assert "Risk" in body
    assert "How to verify" in body


def test_render_pr_body_error():
    merged = _doc("merged", error="all failed")
    body = render_pr_body(merged)
    assert "all failed" in body


# ── config — YAML + inline ────────────────────────────────────────────────


def test_yaml_load_uses_summarizers_or_clis_alias(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("summarizers:\n  - {name: a, cmd: [a]}\n  - {name: b, cmd: [b]}\n")
    cfgs = load_config_yaml(p)
    assert [c.cli for c in cfgs] == ["a", "b"]

    p2 = tmp_path / "c2.yaml"
    p2.write_text("clis:\n  - {name: x, cmd: [x]}\n")
    cfgs2 = load_config_yaml(p2)
    assert cfgs2[0].cli == "x"


def test_yaml_arbitrary_count(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("""
summarizers:
  - {name: a, cmd: [a, -p]}
  - {name: b, cmd: [b, -p]}
  - {name: c, cmd: [c, -p]}
  - {name: ollama, cmd: [ollama, run, qwen2.5], timeout_s: 600}
""")
    cfgs = load_config_yaml(p)
    assert len(cfgs) == 4
    ollama = next(c for c in cfgs if c.cli == "ollama")
    assert ollama.timeout_s == 600


def test_yaml_missing_top_level(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("notlist: 5")
    with pytest.raises(ValueError):
        load_config_yaml(p)


def test_inline_simple():
    cfg = parse_inline_summarizer("claude=claude,-p")
    assert cfg.cli == "claude"
    assert cfg.cmd == ["claude", "-p"]


def test_inline_multi_arg():
    cfg = parse_inline_summarizer("ollama=ollama,run,qwen2.5-coder")
    assert cfg.cli == "ollama"
    assert cfg.cmd == ["ollama", "run", "qwen2.5-coder"]


# ── run_summary with in-process runners ───────────────────────────────────


def test_run_summary_with_runners():
    """Validate the parallel-dispatch path with stub runners (no shell-out)."""
    def factory(name, payload):
        def run(diff, prompt):  # noqa: ARG001
            return SummaryDoc(cli=name, tldr=f"{name}-tldr", files_changed=f"- a.py: {payload}")
        return run

    cfgs = [
        SummaryConfig(cli="a", runner=factory("a", "x")),
        SummaryConfig(cli="b", runner=factory("b", "y")),
        SummaryConfig(cli="c", runner=factory("c", "z")),
    ]
    docs = run_summary("dummy diff", configs=cfgs)
    assert len(docs) == 3
    assert {d.cli for d in docs} == {"a", "b", "c"}


# ── DiffProvider ──────────────────────────────────────────────────────────


def test_static_diff_provider():
    p = StaticDiffProvider("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-foo\n+bar\n")
    assert "bar" in p.fetch("any/repo", 1)
