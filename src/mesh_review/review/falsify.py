"""Sigma adversarial gate — for each consensus finding, ask each CLI to falsify it.

A finding "survives" the gate if at least one CLI defends it convincingly OR
no CLI produces a credible falsification. This filters false positives without
silencing genuine issues nobody wants to defend.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Callable, Dict, List, Optional

from .consensus import ConsensusFinding


_FALSIFY_PROMPT = """You are running an adversarial code review.

Below is a finding flagged by multiple AI reviewers. Your job is to FALSIFY it:
look for reasons this finding is wrong, irrelevant, or a false positive.

Finding:
  file:     {file}
  line:     {line}
  severity: {severity}
  title:    {title}
  detail:   {body}

Output JSON: {{
  "falsified": true|false,
  "confidence": 0.0..1.0,
  "rationale": "<1-2 sentences>"
}}"""


def _default_falsifier(cli: str, prompt: str) -> Dict:
    """Stand-in: in production wires to subprocess/SDK call. Here we no-op."""
    return {"falsified": False, "confidence": 0.0, "rationale": f"{cli}: no-op (no SDK wired)"}


def sigma_gate(consensus: List[ConsensusFinding],
               falsifier: Optional[Callable[[str, str], Dict]] = None,
               clis: Optional[List[str]] = None,
               threshold: float = 0.7) -> List[Dict]:
    """Run the falsification round on each finding.

    Args:
        consensus: clusters from build_consensus
        falsifier: function (cli, prompt) → {falsified, confidence, rationale}
        clis:      list of CLI names to query (default: agreeing_clis from cluster)
        threshold: confidence above which a falsification "wins"

    Returns:
        list of {finding (dict), survived (bool), falsifications (list)} per finding
    """
    f = falsifier or _default_falsifier
    out: List[Dict] = []
    for cluster in consensus:
        prompt = _FALSIFY_PROMPT.format(
            file=cluster.file, line=cluster.line, severity=cluster.severity,
            title=cluster.title, body=cluster.findings[0].body if cluster.findings else "",
        )
        falsifications = []
        target_clis = clis or sorted(set(cluster.clis))
        for cli in target_clis:
            try:
                result = f(cli, prompt)
            except Exception as e:
                result = {"falsified": False, "confidence": 0.0, "rationale": f"err: {e}"}
            falsifications.append({"cli": cli, **result})
        # A finding is FALSIFIED if any CLI produced (falsified=True, confidence>=threshold).
        falsified = any(
            x.get("falsified") and float(x.get("confidence", 0)) >= threshold
            for x in falsifications
        )
        out.append({
            "finding": cluster.to_dict(),
            "survived": not falsified,
            "falsifications": falsifications,
        })
    return out


def render_pr_comments(gate_results: List[Dict]) -> List[Dict]:
    """Convert surviving findings into GitHub PR-comment payloads."""
    comments = []
    for entry in gate_results:
        if not entry["survived"]:
            continue
        f = entry["finding"]
        if f["agreement"] not in ("unanimous", "majority"):
            continue
        body = (
            f"**[triple-review · {f['agreement']}]** "
            f"{', '.join(f['agreeing_clis'])} flagged this:\n\n"
            f"**{f['title']}** _(severity: {f['severity']})_\n\n"
        )
        for ind in f["individual_findings"]:
            body += f"- _{ind['cli']}_: {ind['body']}\n"
        comments.append({
            "path": f["file"],
            "line": f["line"],
            "body": body,
        })
    return comments


def to_json(obj) -> str:
    """Serialize for JSON-friendly artifact output."""
    if hasattr(obj, "to_dict"):
        obj = obj.to_dict()
    elif hasattr(obj, "__dataclass_fields__"):
        obj = asdict(obj)
    return json.dumps(obj, indent=2)
