"""YAML-driven CLI registration.

`triple-review` dispatches to *any* number of CLIs, not just three. The
`ReviewConfig` dataclass already accepts an arbitrary `(name, cmd)` pair —
this module just exposes a YAML loader so users can register their own CLIs
without writing Python.

Example `triple-review.yaml`:

    # Required: each entry registers one CLI to dispatch.
    clis:
      - name: claude
        cmd: ["claude", "-p", "--output-format=text"]
        timeout_s: 300

      - name: gemini
        cmd: ["gemini", "-p"]
        timeout_s: 300

      - name: copilot
        cmd: ["copilot", "-p"]
        timeout_s: 300

      # Add any other CLI — local LLM via Ollama, Mistral CLI, your own SDK shim, etc.
      - name: ollama-qwen
        cmd: ["ollama", "run", "qwen2.5-coder"]
        timeout_s: 600

      - name: my-internal-reviewer
        cmd: ["./scripts/review.sh"]
        timeout_s: 240

The orchestrator loads this file, instantiates a `ReviewConfig` per entry,
and dispatches them all in parallel. The Sigma falsification gate then
asks each registered CLI to challenge each finding regardless of vendor.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from .core import ReviewConfig


def load_config_yaml(path: str | Path) -> List[ReviewConfig]:
    """Load CLI configs from a YAML file.

    Raises FileNotFoundError if the path doesn't exist, ValueError if the
    schema is invalid (missing `clis:` list, missing `name` or `cmd`, etc.).
    """
    try:
        import yaml  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "PyYAML required for YAML configs. Install: pip install pyyaml"
        ) from e
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or "clis" not in data:
        raise ValueError(f"{p}: missing top-level `clis:` list")
    clis = data["clis"]
    if not isinstance(clis, list) or not clis:
        raise ValueError(f"{p}: `clis:` must be a non-empty list")
    configs: List[ReviewConfig] = []
    for i, entry in enumerate(clis):
        if not isinstance(entry, dict):
            raise ValueError(f"{p}: clis[{i}] is not a mapping")
        name = entry.get("name")
        cmd = entry.get("cmd")
        if not name or not isinstance(name, str):
            raise ValueError(f"{p}: clis[{i}] missing required `name`")
        if not cmd or not isinstance(cmd, list):
            raise ValueError(f"{p}: clis[{i}].cmd must be a non-empty list")
        configs.append(ReviewConfig(
            cli=name,
            cmd=[str(x) for x in cmd],
            timeout_s=int(entry.get("timeout_s", 300)),
        ))
    return configs


def parse_inline_cli(spec: str) -> ReviewConfig:
    """Parse a `--cli name=arg1,arg2,...` spec into a ReviewConfig.

    Examples:
        "claude=claude,-p"                           → ReviewConfig(cli=claude, cmd=[claude, -p])
        "ollama-qwen=ollama,run,qwen2.5-coder"       → multi-arg cmd
        "myrev=./review.sh"                          → single binary, no args
    """
    if "=" not in spec:
        raise ValueError(f"--cli spec must be `name=cmd[,arg,...]`, got {spec!r}")
    name, _, rest = spec.partition("=")
    name = name.strip()
    if not name:
        raise ValueError(f"--cli spec missing name: {spec!r}")
    parts = [p for p in rest.split(",") if p]
    if not parts:
        raise ValueError(f"--cli spec missing cmd: {spec!r}")
    return ReviewConfig(cli=name, cmd=parts, timeout_s=300)
