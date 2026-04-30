"""Summary subcommand — multi-LLM PR summarizer (merge or vote aggregation)."""
from .core import SummaryConfig, SummaryDoc, run_summary
from .merge import merge_structural, vote_best, render_pr_body
from .config import load_config_yaml, parse_inline_summarizer
from .diff import GithubDiffProvider, StaticDiffProvider

__all__ = [
    "SummaryConfig", "SummaryDoc", "run_summary",
    "merge_structural", "vote_best", "render_pr_body",
    "load_config_yaml", "parse_inline_summarizer",
    "GithubDiffProvider", "StaticDiffProvider",
]
