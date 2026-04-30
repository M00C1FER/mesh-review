"""Core dispatch — runs the same review prompt across all configured CLIs in parallel."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Callable, Dict, List, Optional


# ── Data shapes ──────────────────────────────────────────────────────────────


# Words too generic to participate in title-based clustering — drop them so
# "Brittle JSON parsing" matches "JSON parsing brittle" matches "parsing JSON".
_TITLE_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "is", "it", "its", "of", "on", "or", "that", "the", "this",
    "to", "was", "were", "will", "with",
})


def _normalize_title(title: str) -> str:
    """Lowercase, strip non-alphanum, drop stopwords, sort tokens.
    Two titles like "Brittle JSON parsing" and "JSON parsing — brittle"
    yield the same key."""
    tokens = re.findall(r"[a-z0-9]+", title.lower())
    tokens = [t for t in tokens if t not in _TITLE_STOPWORDS and len(t) > 1]
    return ":".join(sorted(set(tokens)))


@dataclass
class Finding:
    """A single review finding from one CLI."""
    cli: str
    file: str
    line: Optional[int]
    severity: str   # critical | high | medium | low | info
    title: str
    body: str

    def fingerprint(self) -> str:
        """Stable key used to cluster findings across CLIs.

        Cluster when same-file + same-severity + (line within ±2) + similar title.
        Title is normalized (lowercase, stopwords stripped, sorted tokens) so
        rephrasing across LLMs still matches; line uses ±2 window via floor div by 3
        rather than //5 (the prior //5 had a 5-line blast radius and could
        cluster unrelated findings).
        """
        line_bucket = (self.line // 3) if self.line else 0
        return (
            f"{self.file}:{self.severity}:{line_bucket}:"
            f"{_normalize_title(self.title)}"
        )


@dataclass
class ReviewResult:
    cli: str
    findings: List[Finding]
    raw_output: str
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "cli": self.cli,
            "findings": [asdict(f) for f in self.findings],
            "error": self.error,
        }


@dataclass
class ReviewConfig:
    """Per-CLI launch config. `runner` overrides shell-out for tests / SDK calls."""
    cli: str
    cmd: List[str] = field(default_factory=list)
    runner: Optional[Callable[[str, str], ReviewResult]] = None
    timeout_s: int = 300


# ── Built-in runners (shell-out) ────────────────────────────────────────────


_DEFAULT_PROMPT = """You are running a code review.

Read the file at {path} and produce a JSON list of findings. Each finding has:
  cli (your name: claude/gemini/copilot)
  file
  line
  severity (critical | high | medium | low | info)
  title (short)
  body (1-3 sentences explaining the issue)

Output ONLY a JSON array. No prose. No markdown fences."""


def _shell_runner(cli: str, cmd: List[str], timeout: int):
    """Build a runner that shells out to a CLI binary."""
    def run(path: str, prompt: str) -> ReviewResult:
        if not shutil.which(cmd[0]):
            return ReviewResult(cli=cli, findings=[], raw_output="",
                                error=f"{cmd[0]} not found on PATH")
        full_prompt = prompt.replace("{path}", path)
        try:
            proc = subprocess.run(
                cmd + [full_prompt],
                input=Path(path).read_text(encoding="utf-8", errors="replace"),
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ReviewResult(cli=cli, findings=[], raw_output="",
                                error=f"{cli} timeout after {timeout}s")
        out = proc.stdout
        findings = _parse_findings(out, default_cli=cli, default_file=path)
        return ReviewResult(cli=cli, findings=findings, raw_output=out)
    return run


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL | re.IGNORECASE)


def _try_loads(s: str):
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _parse_findings(text: str, default_cli: str, default_file: str) -> List[Finding]:
    """Best-effort parse of CLI output into Finding[]. Tolerates prose/fences/multiple blocks.

    Resolution order:
      1. Direct JSON (whole stdout is the array).
      2. ```json ... ``` fenced blocks (try each, take first list).
      3. Greedy `[...]` extract: find each candidate `[...]` substring with
         balanced brackets; try each from longest to shortest.
    """
    if not text.strip():
        return []
    # 1. Direct
    data = _try_loads(text)
    # 2. Fenced
    if data is None:
        for match in _JSON_FENCE_RE.finditer(text):
            cand = _try_loads(match.group(1).strip())
            if isinstance(cand, list):
                data = cand
                break
    # 3. Balanced-bracket scan (handles titles containing `[`, multiple arrays)
    if data is None:
        candidates: List[str] = []
        depth = 0
        start_idx = -1
        for i, ch in enumerate(text):
            if ch == "[":
                if depth == 0:
                    start_idx = i
                depth += 1
            elif ch == "]" and depth > 0:
                depth -= 1
                if depth == 0 and start_idx != -1:
                    candidates.append(text[start_idx:i + 1])
                    start_idx = -1
        for cand in sorted(candidates, key=len, reverse=True):
            parsed = _try_loads(cand)
            if isinstance(parsed, list):
                data = parsed
                break
    if not isinstance(data, list):
        return []
    findings: List[Finding] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        findings.append(Finding(
            cli=str(item.get("cli", default_cli)),
            file=str(item.get("file", default_file)),
            line=item.get("line") if isinstance(item.get("line"), int) else None,
            severity=str(item.get("severity", "info")).lower(),
            title=str(item.get("title", ""))[:200],
            body=str(item.get("body", ""))[:2000],
        ))
    return findings


def default_configs() -> List[ReviewConfig]:
    """Bundled 3-CLI preset: claude / gemini / copilot.

    This is one valid configuration, not a hard requirement. The orchestrator
    accepts any number of CLIs >= 1 — register them via:
      • `triple-review --config my.yaml ...` (YAML file)
      • `triple-review --cli name=cmd,arg,... ...` (repeatable inline flag)
      • Programmatic: pass `configs=[ReviewConfig(...)]` to `run_review()`

    See `examples/triple-review.example.yaml` for a multi-CLI YAML.
    """
    return [
        ReviewConfig(cli="claude",  cmd=["claude",  "-p", "--output-format=text"], timeout_s=300),
        ReviewConfig(cli="gemini",  cmd=["gemini",  "-p"],                          timeout_s=300),
        ReviewConfig(cli="copilot", cmd=["copilot", "-p"],                          timeout_s=300),
    ]


# ── Orchestration ───────────────────────────────────────────────────────────


def run_review(path: str, configs: Optional[List[ReviewConfig]] = None,
               prompt: str = _DEFAULT_PROMPT) -> List[ReviewResult]:
    """Run the review prompt across all configured CLIs in parallel.

    Each CLI runs in its own thread; failures don't block siblings.
    Returns list of `ReviewResult` keyed by `cli`.
    """
    cfgs = configs or default_configs()
    results: List[ReviewResult] = []
    with ThreadPoolExecutor(max_workers=len(cfgs)) as pool:
        futures = {}
        for cfg in cfgs:
            runner = cfg.runner or _shell_runner(cfg.cli, cfg.cmd, cfg.timeout_s)
            futures[pool.submit(runner, path, prompt)] = cfg.cli
        for fut in as_completed(futures):
            cli = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                results.append(ReviewResult(cli=cli, findings=[], raw_output="",
                                            error=f"{type(e).__name__}: {e}"))
    return results
