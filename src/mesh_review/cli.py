"""mesh-review CLI — unified entrypoint for review + summary subcommands."""
from __future__ import annotations

import argparse
import json
import sys
from typing import List

from .review.config import load_config_yaml as load_review_yaml, parse_inline_cli
from .review.consensus import build_consensus
from .review.core import ReviewConfig, default_configs as default_review, run_review
from .review.falsify import render_pr_comments, sigma_gate
from .summary.config import load_config_yaml as load_summary_yaml
from .summary.core import SummaryConfig, default_configs as default_summary, run_summary
from .summary.diff import GithubDiffProvider
from .summary.merge import merge_structural, render_pr_body, vote_best


# ── Shared helpers ──────────────────────────────────────────────────────────


def _resolve_review_configs(args) -> List[ReviewConfig]:
    cfgs: List[ReviewConfig] = []
    if args.config:
        cfgs.extend(load_review_yaml(args.config))
    for spec in args.cli or []:
        cfgs.append(parse_inline_cli(spec))
    return cfgs or default_review()


def _resolve_summary_configs(args) -> List[SummaryConfig]:
    cfgs: List[SummaryConfig] = []
    if args.config:
        cfgs.extend(load_summary_yaml(args.config))
    return cfgs or default_summary()


# ── review subcommand ───────────────────────────────────────────────────────


def cmd_review(args) -> int:
    configs = _resolve_review_configs(args)
    if args.list_clis:
        for c in configs:
            print(f"  {c.cli:24} cmd={c.cmd}  timeout={c.timeout_s}s")
        return 0
    all_results = []
    for path in args.files:
        all_results.extend(run_review(path, configs=configs))
    consensus = build_consensus(all_results)
    if args.falsify:
        gate = sigma_gate(consensus, clis=[c.cli for c in configs])
        if args.pr_comments:
            print(json.dumps(render_pr_comments(gate), indent=2))
        elif args.json:
            print(json.dumps(gate, indent=2))
        else:
            for entry in gate:
                f = entry["finding"]
                marker = "[SURVIVED]" if entry["survived"] else "[FALSIFIED]"
                print(f"{marker} {f['agreement']}/{f['severity']} {f['file']}:{f['line']} — {f['title']}")
        return 0
    if args.json:
        print(json.dumps([c.to_dict() for c in consensus], indent=2))
    else:
        for c in consensus:
            print(f"[{c.agreement}/{c.severity}] {c.file}:{c.line} — {c.title}")
    return 0


# ── summary subcommand ──────────────────────────────────────────────────────


def cmd_summary(args) -> int:
    configs = _resolve_summary_configs(args)
    if args.list_clis:
        for c in configs:
            print(f"  {c.cli:24} cmd={c.cmd}  timeout={c.timeout_s}s")
        return 0
    if not args.pr and not args.diff_file:
        print("error: provide --pr <repo>#<n> or --diff-file <path>", file=sys.stderr)
        return 2
    if args.diff_file:
        with open(args.diff_file, encoding="utf-8") as f:
            diff = f.read()
    else:
        repo, _, pr = args.pr.partition("#")
        if not repo or not pr:
            print(f"error: --pr must be `owner/repo#N`, got {args.pr!r}", file=sys.stderr)
            return 2
        diff = GithubDiffProvider().fetch(repo, pr)
    docs = run_summary(diff, configs=configs)
    merged = merge_structural(docs) if args.mode == "merge" else vote_best(docs)
    if args.render == "raw":
        print(merged.raw or "")
    elif args.render == "json":
        print(json.dumps(merged.to_dict(), indent=2))
    else:
        print(render_pr_body(merged))
    return 0 if not merged.error else 1


# ── argparse plumbing ───────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="mesh-review",
        description="Modular multi-LLM PR review + summary toolkit (vendor-neutral CLI registry).",
        epilog="Subcommands: review · summary. Both share the same YAML CLI registry.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # review
    pr = sub.add_parser("review", help="multi-LLM consensus + Sigma falsification gate")
    pr.add_argument("files", nargs="+")
    pr.add_argument("--config", metavar="PATH")
    pr.add_argument("--cli", action="append", metavar="NAME=CMD[,ARG,...]")
    pr.add_argument("--falsify", action="store_true")
    pr.add_argument("--pr-comments", action="store_true")
    pr.add_argument("--json", action="store_true")
    pr.add_argument("--list-clis", action="store_true")
    pr.set_defaults(func=cmd_review)

    # summary
    ps = sub.add_parser("summary", help="multi-LLM PR summarizer (merge / vote)")
    ps.add_argument("--config", metavar="PATH")
    ps.add_argument("--pr", help="owner/repo#N (uses gh pr diff)")
    ps.add_argument("--diff-file")
    ps.add_argument("--mode", choices=["merge", "vote"], default="merge")
    ps.add_argument("--render", choices=["pr-body", "raw", "json"], default="pr-body")
    ps.add_argument("--list-clis", action="store_true")
    ps.set_defaults(func=cmd_summary)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
