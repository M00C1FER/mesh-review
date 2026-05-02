"""Golden test using examples/broken-repo/auth.py.

Validates that the Sigma falsification gate eliminates a known false positive
(MD5 used as a non-crypto checksum) without dropping the known true positives
(SQL injection, hard-coded secret, insecure deserialisation, plaintext password
comparison).

This test uses in-process runners and a calibrated mock falsifier so no real
LLM CLI needs to be installed in CI.
"""
from __future__ import annotations

from pathlib import Path

from mesh_review.review.consensus import build_consensus
from mesh_review.review.core import Finding, ReviewConfig, ReviewResult, run_review
from mesh_review.review.falsify import render_pr_comments, sigma_gate

_BROKEN_REPO = Path(__file__).parent.parent / "examples" / "broken-repo" / "auth.py"

# ── Simulated findings ─────────────────────────────────────────────────────
# Four true positives + one near-false-positive (MD5 for non-crypto use).
# All three mock CLIs agree on them to produce unanimous clusters.

_TRUE_POSITIVES = [
    ("sql-injection-login", "critical", 31),
    ("hardcoded-secret", "high", 16),
    ("insecure-deserialisation", "high", 54),
    ("plaintext-password-comparison", "medium", 35),
]
_FALSE_POSITIVE = ("md5-non-crypto-checksum", "low", 44)  # defensible


def _make_findings(cli: str) -> list[Finding]:
    findings = [
        Finding(cli=cli, file=str(_BROKEN_REPO), line=line, severity=sev,
                title=title, body=f"{cli}: {title}")
        for title, sev, line in _TRUE_POSITIVES
    ]
    # Add the near-false-positive
    fp_title, fp_sev, fp_line = _FALSE_POSITIVE
    findings.append(Finding(cli=cli, file=str(_BROKEN_REPO), line=fp_line,
                            severity=fp_sev, title=fp_title,
                            body=f"{cli}: {fp_title}"))
    return findings


def _fake_runner(cli):
    """Return an in-process runner that emits the simulated findings."""
    def run(path, prompt):  # noqa: ARG001
        return ReviewResult(cli=cli, findings=_make_findings(cli), raw_output="")
    return run


def _calibrated_falsifier(cli: str, prompt: str) -> dict:
    """Mock falsifier that correctly identifies the MD5 false positive.

    In a real deployment this would call an LLM SDK. Here it inspects the
    prompt string directly so the test is deterministic and CI-safe.
    """
    if "md5" in prompt.lower() and "non-crypto" in prompt.lower():
        return {"falsified": True, "confidence": 0.85,
                "rationale": f"{cli}: MD5 is acceptable for non-cryptographic checksums"}
    return {"falsified": False, "confidence": 0.0, "rationale": "genuine issue"}


# ── Tests ──────────────────────────────────────────────────────────────────


def test_broken_repo_file_exists():
    """Sanity-check that the example file is present in the repo."""
    assert _BROKEN_REPO.exists(), (
        f"examples/broken-repo/auth.py not found at {_BROKEN_REPO}; "
        "create it before running golden tests."
    )


def test_golden_gate_drops_false_positive_keeps_true_positives():
    """Gate eliminates MD5 false positive; all 4 true positives survive."""
    cfgs = [
        ReviewConfig(cli="claude",  runner=_fake_runner("claude")),
        ReviewConfig(cli="gemini",  runner=_fake_runner("gemini")),
        ReviewConfig(cli="copilot", runner=_fake_runner("copilot")),
    ]
    results = run_review(str(_BROKEN_REPO), configs=cfgs)
    assert len(results) == 3

    consensus = build_consensus(results)
    # All 5 findings are unanimous (3 CLIs agree on each)
    assert all(c.agreement == "unanimous" for c in consensus)
    assert len(consensus) == 5

    gate = sigma_gate(consensus, falsifier=_calibrated_falsifier)

    survived = [e for e in gate if e["survived"]]
    dropped  = [e for e in gate if not e["survived"]]

    # Exactly one finding should be dropped (the MD5 false positive)
    assert len(dropped) == 1, f"Expected 1 dropped finding, got {len(dropped)}: {dropped}"
    assert _FALSE_POSITIVE[0] in dropped[0]["finding"]["title"]

    # All four true positives must survive
    survived_titles = {e["finding"]["title"] for e in survived}
    for title, _sev, _line in _TRUE_POSITIVES:
        assert title in survived_titles, f"True positive '{title}' was incorrectly dropped"


def test_golden_gate_pr_comments_contain_true_positives():
    """render_pr_comments produces one comment per surviving unanimous finding."""
    cfgs = [
        ReviewConfig(cli="claude",  runner=_fake_runner("claude")),
        ReviewConfig(cli="gemini",  runner=_fake_runner("gemini")),
        ReviewConfig(cli="copilot", runner=_fake_runner("copilot")),
    ]
    results = run_review(str(_BROKEN_REPO), configs=cfgs)
    consensus = build_consensus(results)
    gate = sigma_gate(consensus, falsifier=_calibrated_falsifier)
    comments = render_pr_comments(gate)

    # 4 true positives, each unanimous → 4 PR comments
    assert len(comments) == 4
    bodies = " ".join(c["body"] for c in comments)
    assert "sql-injection-login" in bodies
    assert "hardcoded-secret" in bodies
    # The false positive must NOT appear in the comments
    assert _FALSE_POSITIVE[0] not in bodies


def test_golden_gate_no_falsifier_all_survive():
    """Without a falsifier, all 5 findings (including the false positive) survive."""
    cfgs = [
        ReviewConfig(cli="claude",  runner=_fake_runner("claude")),
        ReviewConfig(cli="gemini",  runner=_fake_runner("gemini")),
        ReviewConfig(cli="copilot", runner=_fake_runner("copilot")),
    ]
    results = run_review(str(_BROKEN_REPO), configs=cfgs)
    consensus = build_consensus(results)
    # Default no-op falsifier: nothing gets falsified
    gate = sigma_gate(consensus)
    assert all(e["survived"] for e in gate)
    assert len(gate) == 5
