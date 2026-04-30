"""Tests for YAML config + inline CLI parsing."""
from __future__ import annotations

import pytest

from mesh_review.review.config import load_config_yaml, parse_inline_cli


def test_yaml_load_minimum(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("""
clis:
  - name: claude
    cmd: ["claude", "-p"]
""")
    cfgs = load_config_yaml(p)
    assert len(cfgs) == 1
    assert cfgs[0].cli == "claude"
    assert cfgs[0].cmd == ["claude", "-p"]
    assert cfgs[0].timeout_s == 300  # default


def test_yaml_load_arbitrary_cli_count(tmp_path):
    """The orchestrator works with N CLIs, not just 3."""
    p = tmp_path / "c.yaml"
    p.write_text("""
clis:
  - { name: claude,  cmd: [claude, -p] }
  - { name: gemini,  cmd: [gemini, -p] }
  - { name: copilot, cmd: [copilot, -p] }
  - { name: ollama,  cmd: [ollama, run, qwen2.5], timeout_s: 600 }
  - { name: custom,  cmd: ["./script.sh"] }
""")
    cfgs = load_config_yaml(p)
    assert len(cfgs) == 5
    assert {c.cli for c in cfgs} == {"claude", "gemini", "copilot", "ollama", "custom"}
    ollama = next(c for c in cfgs if c.cli == "ollama")
    assert ollama.timeout_s == 600


def test_yaml_missing_clis_key(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("not_clis: []")
    with pytest.raises(ValueError, match="clis"):
        load_config_yaml(p)


def test_yaml_empty_clis(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("clis: []")
    with pytest.raises(ValueError, match="non-empty"):
        load_config_yaml(p)


def test_yaml_missing_name(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("clis:\n  - cmd: [foo]\n")
    with pytest.raises(ValueError, match="name"):
        load_config_yaml(p)


def test_yaml_missing_cmd(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("clis:\n  - name: foo\n")
    with pytest.raises(ValueError, match="cmd"):
        load_config_yaml(p)


def test_yaml_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_config_yaml("/tmp/does-not-exist-1234.yaml")


def test_inline_simple():
    cfg = parse_inline_cli("claude=claude,-p")
    assert cfg.cli == "claude"
    assert cfg.cmd == ["claude", "-p"]


def test_inline_multi_arg():
    cfg = parse_inline_cli("ollama-coder=ollama,run,qwen2.5-coder")
    assert cfg.cli == "ollama-coder"
    assert cfg.cmd == ["ollama", "run", "qwen2.5-coder"]


def test_inline_single_binary():
    cfg = parse_inline_cli("myrev=./review.sh")
    assert cfg.cli == "myrev"
    assert cfg.cmd == ["./review.sh"]


def test_inline_missing_equals():
    with pytest.raises(ValueError, match="name=cmd"):
        parse_inline_cli("nosignhere")


def test_inline_missing_name():
    with pytest.raises(ValueError, match="name"):
        parse_inline_cli("=claude,-p")


def test_inline_missing_cmd():
    with pytest.raises(ValueError, match="cmd"):
        parse_inline_cli("claude=")
