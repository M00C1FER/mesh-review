"""Aggregate multiple SummaryDoc objects into one consolidated output.

Two modes:
  • merge_structural — concatenate sections with per-CLI attribution; near-
                       duplicate paragraphs collapsed via SequenceMatcher
                       similarity
  • vote_best        — pick the single SummaryDoc whose sections are the
                       longest non-empty (proxy for "most thorough")
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import List

from .core import SummaryDoc


# Two paragraphs are considered duplicates when their similarity ratio is
# at or above this threshold. 0.85 is empirically tight enough to dedup
# rephrasings ("Adds X" vs "Add X") without collapsing distinct points.
_DUP_THRESHOLD = 0.85


def _normalize_for_dedup(s: str) -> str:
    """Lowercase + strip non-alphanum so `Adds JWT auth` and `Add JWT auth.`
    are treated as identical for similarity scoring."""
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _is_near_dup(a: str, b: str) -> bool:
    na, nb = _normalize_for_dedup(a), _normalize_for_dedup(b)
    if not na or not nb:
        return False
    return SequenceMatcher(None, na, nb).ratio() >= _DUP_THRESHOLD


def merge_structural(docs: List[SummaryDoc]) -> SummaryDoc:
    """Build a single SummaryDoc by concatenating per-CLI contributions.

    Each section becomes a multi-paragraph block with `[cli]` prefixes so
    reviewers can trace back to source. Near-duplicate paragraphs (≥85%
    similarity, normalized) are collapsed into a single attribution
    "[claude+gemini]" to avoid noise when multiple CLIs say the same thing.
    Errors are surfaced as a footnote.
    """
    valid = [d for d in docs if not d.error and not d.is_empty()]
    errored = [d for d in docs if d.error]

    if not valid:
        msg = "All summarizers failed or returned empty."
        if errored:
            msg += " " + "; ".join(f"{d.cli}: {d.error}" for d in errored)
        return SummaryDoc(cli="merged", error=msg)

    def join(field: str) -> str:
        # First pass — collect (cli, value) pairs, skipping empties
        items: List[tuple[str, str]] = []
        for d in valid:
            value = getattr(d, field, "").strip()
            if value:
                items.append((d.cli, value))
        # Second pass — cluster near-duplicates; first encountered wins as
        # the canonical text, every dup adds its CLI to the attribution list.
        clusters: List[tuple[List[str], str]] = []
        for cli, value in items:
            placed = False
            for i, (clis, canon) in enumerate(clusters):
                if _is_near_dup(canon, value):
                    clusters[i] = (clis + [cli], canon)
                    placed = True
                    break
            if not placed:
                clusters.append(([cli], value))
        # Render with `[cli1+cli2] body` per cluster
        return "\n\n".join(f"[{'+'.join(clis)}] {canon}" for clis, canon in clusters)

    merged = SummaryDoc(
        cli="merged",
        tldr=join("tldr"),
        files_changed=join("files_changed"),
        risk=join("risk"),
        test_plan=join("test_plan"),
    )
    if errored:
        # Append degraded-vendors footnote
        notes = "\n_Note: " + ", ".join(f"{d.cli} ({d.error})" for d in errored) + "_"
        merged.tldr = (merged.tldr + notes) if merged.tldr else notes
    return merged


def vote_best(docs: List[SummaryDoc]) -> SummaryDoc:
    """Pick the SummaryDoc with the most filled-in content (proxy for thoroughness)."""
    valid = [d for d in docs if not d.error and not d.is_empty()]
    if not valid:
        return SummaryDoc(cli="vote", error="no valid summaries")

    def score(d: SummaryDoc) -> int:
        return sum(len(getattr(d, f, "")) for f in ("tldr", "files_changed", "risk", "test_plan"))

    return max(valid, key=score)


def render_pr_body(merged: SummaryDoc, marker: str = "<!-- pr-summary-mesh -->") -> str:
    """Format a merged SummaryDoc as a PR description body fragment.

    Wraps the output in marker comments so an updater can find/replace it
    on subsequent runs.
    """
    if merged.error:
        return f"{marker}\n_pr-summary-mesh: {merged.error}_\n{marker}\n"
    parts = [marker, "## Summary (auto-generated)"]
    if merged.tldr:
        parts.append("**TL;DR**\n\n" + merged.tldr + "\n")
    if merged.files_changed:
        parts.append("**Changed files**\n\n" + merged.files_changed + "\n")
    if merged.risk:
        parts.append("**Risk**\n\n" + merged.risk + "\n")
    if merged.test_plan:
        parts.append("**How to verify**\n\n" + merged.test_plan + "\n")
    parts.append(marker)
    return "\n".join(parts) + "\n"
