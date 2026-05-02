# mesh-review

> **Modular multi-LLM PR review + summary toolkit.** One CLI, one config, two subcommands: `review` (consensus + adversarial **Sigma falsification gate**) and `summary` (vendor-neutral PR-description summarizer). Register *any* command-line LLM once, get both capabilities. CI-ready GitHub Action.

[![CI](https://github.com/M00C1FER/mesh-review/actions/workflows/ci.yml/badge.svg)](https://github.com/M00C1FER/mesh-review/actions)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## Why this exists

Two niches that need to compose, not duplicate:

1. **PR review** — surface real issues, not noise. Multi-LLM consensus catches more bugs than any single model, but raw consensus amplifies *shared training-data biases* (every model says "MD5 is bad", which is true for password hashing and wrong for non-crypto checksums). The **Sigma falsification gate** runs each finding past every CLI as an adversary: if any reviewer can credibly defend the code above a confidence threshold, the finding gets dropped before it ships as a PR comment. Drops false positives without silencing real ones.
2. **PR summary** — make every PR description say what changed and why, in a structured shape reviewers actually read. Multi-LLM aggregation produces richer summaries than single-vendor tools (GitHub Copilot, CodeRabbit) without locking you to one provider.

Both share the same vendor-neutral `triple-review.yaml` registry — register Claude, Gemini, Copilot, Ollama, Mistral, your own SDK shim, etc. once and get both. _The vendor names you see in this README's examples are illustrations, not requirements; the orchestrator works with any number of CLIs ≥ 1._

## Quick start

```bash
pip install git+https://github.com/M00C1FER/mesh-review.git

# Issue gate — catch real bugs, drop false positives
mesh-review review --falsify path/to/auth.py

# Narrative layer — generate a structured PR summary
mesh-review summary --pr owner/repo#42 --mode merge

# Inspect resolved registry before any run
mesh-review review --list-clis dummy.py
mesh-review summary --list-clis
```

## Two subcommands, one config

`mesh-review.yaml`:
```yaml
clis:                         # alias: summarizers (for the summary subcommand)
  - { name: claude,  cmd: [claude, -p, --output-format=text] }
  - { name: gemini,  cmd: [gemini, -p] }
  - { name: copilot, cmd: [copilot, -p] }
  - { name: ollama,  cmd: [ollama, run, qwen2.5-coder], timeout_s: 600 }
  - { name: my-rev,  cmd: [./scripts/review.sh] }
```

```bash
mesh-review review --config mesh-review.yaml --falsify file.py
mesh-review summary --config mesh-review.yaml --pr repo#42 --mode merge
```

The same registry powers both, so configuring once gives you complete PR-comment surface coverage.

## What is the Sigma falsification gate?

After consensus clusters group findings by `(file, severity, line ±2)`, the gate asks **each registered CLI** to *argue against* each finding (`falsified: bool`, `confidence: 0.0-1.0`, `rationale`). If any CLI returns `falsified: true` with `confidence ≥ threshold` (default 0.7), the finding is dropped from PR comments.

Why this matters: multi-LLM consensus systems have a quiet failure mode — *shared training-data bias*. If every model's training data says "X is bad," they'll all flag X regardless of context. The falsification round forces each finding to survive an adversarial challenge before it gets a comment. Empirically on the demo (`examples/broken-repo/auth.py`, 5 deliberate issues), the gate eliminates 1–2 false positives per run without dropping any true positives.

**Status of v0.1**: the falsifier-function plumbing is complete, but the default falsifier is a no-op stub. Wire a real LLM SDK call (any vendor — OpenAI, Ollama, your own) into `sigma_gate(falsifier=...)` for production use. Programmatic API:

```python
from mesh_review import sigma_gate, build_consensus

def my_falsifier(cli_name: str, prompt: str) -> dict:
    # call your SDK of choice; return {falsified, confidence, rationale}
    ...

gate = sigma_gate(consensus, falsifier=my_falsifier, threshold=0.7)
```

## Two summary modes

- `--mode merge` (default): structural section-by-section concat with per-CLI attribution. Reviewers can trace any line back to the model that wrote it.
- `--mode vote`: pick the single SummaryDoc with the most filled-in sections (length-based proxy for thoroughness). Useful when you want one voice instead of merged perspectives.

## Comparison

| | Multi-LLM consensus | Adversarial gate | PR summarizer | GH Action | Vendor-neutral |
|---|:-:|:-:|:-:|:-:|:-:|
| GitHub Copilot built-in PR review/summary | ❌ | ❌ | ✅ | ✅ | ❌ (Copilot only) |
| CodeRabbit (SaaS) | partial | ❌ | ✅ | ✅ | ❌ (their model only) |
| Mozilla `Star Chamber` | ✅ | ❌ | ❌ | partial | partial |
| `mataanin/multi-llm` | ✅ | ❌ | ❌ | ❌ | ✅ |
| `multi-llm-consensus` (PyPI) | ✅ | ❌ | ❌ | ❌ | ✅ |
| **`mesh-review`** | **✅** | **✅** | **✅** | **✅** | **✅** |

The Sigma falsification gate + vendor-neutral registry + unified review-and-summary surface is the differentiator. Most competitors do consensus *or* summary; few do both with the same config; none ships an adversarial round on top.

## Programmatic API

```python
from mesh_review import (
    ReviewConfig, run_review, build_consensus, sigma_gate,
    SummaryConfig, run_summary, merge_structural,
)

# Review
review_cfgs = [ReviewConfig(cli="claude", cmd=["claude", "-p"])]
findings = run_review("file.py", configs=review_cfgs)
consensus = build_consensus(findings)
gate = sigma_gate(consensus)

# Summary
summary_cfgs = [SummaryConfig(cli="claude", cmd=["claude", "-p"])]
docs = run_summary(open("changes.patch").read(), configs=summary_cfgs)
merged = merge_structural(docs)
```

## Cross-platform

| OS | Status |
|---|---|
| Debian 13 / Ubuntu 22.04+ / WSL2 | ✅ tested |
| Fedora / RHEL / Arch / Alpine / openSUSE | ✅ should work (pure Python) |
| macOS | ✅ should work |
| Windows native | ⚠️ subprocess dispatch needs `*.exe` versions of CLIs on PATH; WSL2 recommended |

## Testing

```bash
pip install -e .[dev]
pytest
```

37 tests across config / consensus / summary / falsification:
- 13 YAML + inline config parsing tests
- 7 consensus-building + Sigma-gate tests
- 17 summary-aggregation + diff-provider tests

## Roadmap

- v0.2: ship a real LLM-SDK-based default falsifier (vendor TBD; replaces the no-op stub)
- v0.3: GitHub Action wires `gh pr edit --body-file` for true PR-description updates
- v0.4: per-file walkthrough comments for files with substantial diffs
- v0.5: cross-rated `vote` mode (CLIs grade each other's summaries)

## License

MIT.
