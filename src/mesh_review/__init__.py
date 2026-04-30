"""mesh-review — modular multi-LLM PR review + summary toolkit.

Two subcommands behind a shared CLI registry:
  mesh-review review  ...   Multi-LLM consensus + adversarial Sigma falsification gate
  mesh-review summary ...   Multi-LLM PR description summarizer (merge / vote)

The same triple-review.yaml powers both: register any command-line LLM once,
get both review and summary capability.
"""
from .review.core import Finding, ReviewConfig, ReviewResult, run_review
from .review.consensus import ConsensusFinding, build_consensus
from .review.falsify import sigma_gate
from .summary.core import SummaryConfig, SummaryDoc, run_summary
from .summary.merge import merge_structural, vote_best, render_pr_body
from .summary.diff import GithubDiffProvider, StaticDiffProvider

__version__ = "0.1.0"
__all__ = [
    "Finding", "ReviewConfig", "ReviewResult", "run_review",
    "ConsensusFinding", "build_consensus", "sigma_gate",
    "SummaryConfig", "SummaryDoc", "run_summary",
    "merge_structural", "vote_best", "render_pr_body",
    "GithubDiffProvider", "StaticDiffProvider",
]
