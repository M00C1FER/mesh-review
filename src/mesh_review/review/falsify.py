"""Sigma adversarial gate — for each consensus finding, ask each CLI to falsify it.

A finding "survives" the gate if at least one CLI defends it convincingly OR
no CLI produces a credible falsification. This filters false positives without
silencing genuine issues nobody wants to defend.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict
from typing import Callable, Dict, List, Optional

from .consensus import ConsensusFinding
from .core import ReviewConfig


_FALSIFY_PROMPT = """You are running an adversarial code review.

Below is a finding flagged by multiple AI reviewers. Your job is to FALSIFY it:
look for reasons this finding is wrong, irrelevant, or a false positive.

Finding:
  file:     {file}
  line:     {line}
  severity: {severity}
  title:    {title}
  detail:   {body}

Output ONLY a JSON object (no prose, no fences):
{{
  "falsified": true|false,
  "confidence": 0.0..1.0,
  "rationale": "<1-2 sentences>"
}}"""


def _balanced_object_substrings(text: str) -> List[str]:
    """Yield every `{ ... }` substring with balanced braces, longest first.

    Handles nested objects (which the prior `\\{[^{}]*\\}` regex couldn't),
    and strings containing braces (a `"`-aware parser walks past them).
    """
    out: List[str] = []
    in_str = False
    escaped = False
    depth = 0
    start_idx = -1
    for i, ch in enumerate(text):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_str:
            escaped = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            if depth == 0:
                start_idx = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start_idx != -1:
                out.append(text[start_idx:i + 1])
                start_idx = -1
    return sorted(out, key=len, reverse=True)


def _parse_falsifier_output(raw: str) -> Optional[Dict]:
    """Tolerate prose/fences/nested objects. Returns None if no usable JSON."""
    if not raw.strip():
        return None
    # Strip ```json ... ``` fences first
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL | re.IGNORECASE)
    if fence_match:
        raw = fence_match.group(1)
    # Try direct
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # Balanced-brace scan (handles nested {} and braces inside string values).
    # Prefer the longest object that contains the "falsified" key.
    for cand in _balanced_object_substrings(raw):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict) and "falsified" in obj:
                return obj
        except json.JSONDecodeError:
            continue
    return None


def make_subprocess_falsifier(
    configs: List[ReviewConfig],
    timeout_s: int = 180,
) -> Callable[[str, str], Dict]:
    """Build a real falsifier that dispatches to the named CLIs via subprocess.

    Each `(cli, prompt)` invocation locates the matching `ReviewConfig.cmd`
    and shells out. If the CLI binary is missing, returns a non-falsifying
    "binary not on PATH" result so the finding survives by default.
    """
    by_name: Dict[str, ReviewConfig] = {c.cli: c for c in configs}

    def falsifier(cli: str, prompt: str) -> Dict:
        cfg = by_name.get(cli)
        if cfg is None or not cfg.cmd:
            return {"falsified": False, "confidence": 0.0,
                    "rationale": f"{cli}: not registered"}
        if not shutil.which(cfg.cmd[0]):
            return {"falsified": False, "confidence": 0.0,
                    "rationale": f"{cli}: {cfg.cmd[0]} not on PATH"}
        try:
            proc = subprocess.run(
                cfg.cmd + [prompt],
                capture_output=True, text=True, timeout=timeout_s,
                input="",
            )
        except subprocess.TimeoutExpired:
            return {"falsified": False, "confidence": 0.0,
                    "rationale": f"{cli}: timeout after {timeout_s}s"}
        if proc.returncode != 0:
            return {"falsified": False, "confidence": 0.0,
                    "rationale": f"{cli}: exited {proc.returncode}: {proc.stderr.strip()[:200]}"}
        parsed = _parse_falsifier_output(proc.stdout)
        if parsed is None:
            return {"falsified": False, "confidence": 0.0,
                    "rationale": f"{cli}: unparseable output"}
        return {
            "falsified": bool(parsed.get("falsified", False)),
            "confidence": min(1.0, max(0.0, float(parsed.get("confidence", 0.0)))),
            "rationale": str(parsed.get("rationale", ""))[:500],
        }

    return falsifier


def _default_falsifier(cli: str, prompt: str) -> Dict:
    """No-op fallback used only when callers don't supply one and we can't
    pick CLI configs. Kept for backwards compatibility / unit tests.
    Prefer `make_subprocess_falsifier(configs)` in production callers."""
    return {"falsified": False, "confidence": 0.0,
            "rationale": f"{cli}: no falsifier supplied (use make_subprocess_falsifier)"}


def _esc(v: object) -> str:
    """Escape curly braces in LLM-controlled values before str.format() calls."""
    return str(v).replace("{", "{{").replace("}", "}}")


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
            file=_esc(cluster.file), line=_esc(cluster.line),
            severity=_esc(cluster.severity), title=_esc(cluster.title),
            body=_esc(cluster.findings[0].body if cluster.findings else ""),
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
