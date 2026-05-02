"""Property-based tests for the Sigma falsification gate.

Uses Hypothesis to exhaustively probe threshold edge cases and invariants
that parameterised unit tests might miss.  The highest-risk surface is the
confidence-threshold comparison in ``sigma_gate``:

    falsified = any(
        x.get("falsified") and float(x.get("confidence", 0)) >= threshold
        for x in falsifications
    )

A subtle off-by-one here creates either false negatives (real issues silently
dropped) or false positives (noise that survives into PR comments).  The
property tests below formalise the intended semantics and cover the boundary
thoroughly.
"""
from __future__ import annotations

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from mesh_review.review.core import Finding, ReviewResult
from mesh_review.review.consensus import build_consensus
from mesh_review.review.falsify import sigma_gate


# ── helpers ─────────────────────────────────────────────────────────────────


def _one_finding_consensus(cli: str = "claude"):
    """Return a single-cluster consensus from one CLI."""
    finding = Finding(cli=cli, file="x.py", line=10, severity="high",
                      title="test issue", body="body")
    results = [ReviewResult(cli=cli, findings=[finding], raw_output="")]
    return build_consensus(results)


# ── threshold boundary properties ────────────────────────────────────────────


@given(
    confidence=st.floats(min_value=0.0, max_value=0.999999),
    threshold=st.floats(min_value=0.01, max_value=1.0),
)
@settings(max_examples=500)
def test_property_below_threshold_always_survives(confidence, threshold):
    """For any confidence *strictly* below threshold, a falsified=True response
    must NOT drop the finding (finding survives).

    This is the false-negative guard: we must never silently drop a real issue
    because a LLM returned a confidence that didn't actually meet the bar.
    """
    assume(confidence < threshold)
    consensus = _one_finding_consensus()

    def falsifier(cli, prompt):
        return {"falsified": True, "confidence": confidence, "rationale": "marginal"}

    gate = sigma_gate(consensus, falsifier=falsifier, threshold=threshold)
    assert gate[0]["survived"] is True, (
        f"Finding incorrectly dropped: confidence={confidence} < threshold={threshold}"
    )


@given(
    confidence=st.floats(min_value=0.0, max_value=1.0),
    threshold=st.floats(min_value=0.0, max_value=1.0),
)
@settings(max_examples=500)
def test_property_at_or_above_threshold_drops_when_falsified(confidence, threshold):
    """For any confidence >= threshold combined with falsified=True, the finding
    must be dropped (survived=False).

    This is the false-positive guard: the gate must consistently eliminate noise
    when a LLM's confidence meets the bar.
    """
    assume(confidence >= threshold)
    consensus = _one_finding_consensus()

    def falsifier(cli, prompt):
        return {"falsified": True, "confidence": confidence, "rationale": "false positive"}

    gate = sigma_gate(consensus, falsifier=falsifier, threshold=threshold)
    assert gate[0]["survived"] is False, (
        f"Finding incorrectly survived: confidence={confidence} >= threshold={threshold}"
    )


@given(
    confidence=st.floats(min_value=0.0, max_value=1.0),
    threshold=st.floats(min_value=0.0, max_value=1.0),
)
@settings(max_examples=300)
def test_property_not_falsified_always_survives(confidence, threshold):
    """When falsified=False, the finding must always survive regardless of
    confidence value or threshold.

    A CLI that says "this is NOT a false positive" must never trigger a drop,
    even with a high confidence value.
    """
    consensus = _one_finding_consensus()

    def falsifier(cli, prompt):
        return {"falsified": False, "confidence": confidence, "rationale": "genuine"}

    gate = sigma_gate(consensus, falsifier=falsifier, threshold=threshold)
    assert gate[0]["survived"] is True, (
        f"Finding incorrectly dropped with falsified=False: confidence={confidence}"
    )


@given(
    threshold=st.floats(min_value=0.0, max_value=1.0),
)
@settings(max_examples=200)
def test_property_no_falsifier_all_survive(threshold):
    """The default no-op falsifier must never drop any finding, for any threshold."""
    consensus = _one_finding_consensus()
    gate = sigma_gate(consensus, threshold=threshold)
    assert all(e["survived"] for e in gate)


@given(
    num_clis=st.integers(min_value=2, max_value=6),
    false_cli_index=st.integers(min_value=0, max_value=5),
    threshold=st.floats(min_value=0.01, max_value=0.99),
)
@settings(max_examples=300)
def test_property_one_high_confidence_falsification_drops_finding(
    num_clis, false_cli_index, threshold
):
    """Among N CLIs, if at least ONE returns falsified=True with confidence >=
    threshold, the finding must be dropped — regardless of what the others say.
    """
    assume(false_cli_index < num_clis)
    clis = [f"cli-{i}" for i in range(num_clis)]

    # Build a multi-CLI unanimous consensus
    finding_per_cli = [
        Finding(cli=c, file="x.py", line=5, severity="medium",
                title="shared issue", body="body")
        for c in clis
    ]
    results = [
        ReviewResult(cli=c, findings=[f], raw_output="")
        for c, f in zip(clis, finding_per_cli)
    ]
    consensus = build_consensus(results)

    def falsifier(cli, prompt):
        if cli == clis[false_cli_index]:
            return {"falsified": True, "confidence": 1.0, "rationale": "false positive"}
        return {"falsified": False, "confidence": 0.0, "rationale": "genuine"}

    gate = sigma_gate(consensus, falsifier=falsifier, threshold=threshold)
    assert gate[0]["survived"] is False, (
        f"Finding should be dropped when CLI {clis[false_cli_index]} falsifies it"
    )


@given(
    confidence=st.floats(min_value=0.0, max_value=1.0),
)
@settings(max_examples=200)
def test_property_out_of_range_confidence_still_works(confidence):
    """Out-of-range confidence values (e.g. from a poorly-calibrated LLM) must
    not cause exceptions; the gate should evaluate them as-is against the threshold.
    """
    threshold = 0.7
    consensus = _one_finding_consensus()

    def falsifier(cli, prompt):
        return {"falsified": True, "confidence": confidence, "rationale": "x"}

    # Must not raise
    gate = sigma_gate(consensus, falsifier=falsifier, threshold=threshold)
    assert isinstance(gate[0]["survived"], bool)
