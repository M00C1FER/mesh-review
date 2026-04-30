"""Consensus building — clusters findings across CLIs and tags by agreement level."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List

from .core import Finding, ReviewResult


@dataclass
class ConsensusFinding:
    """A finding (or near-duplicate cluster) with cross-CLI agreement metadata."""
    findings: List[Finding] = field(default_factory=list)
    clis: List[str] = field(default_factory=list)
    fingerprint: str = ""

    @property
    def title(self) -> str:
        return self.findings[0].title if self.findings else ""

    @property
    def severity(self) -> str:
        # Take the worst severity reported across the cluster.
        order = ["critical", "high", "medium", "low", "info"]
        for s in order:
            if any(f.severity == s for f in self.findings):
                return s
        return "info"

    @property
    def file(self) -> str:
        return self.findings[0].file if self.findings else ""

    @property
    def line(self):
        return self.findings[0].line if self.findings else None

    @property
    def agreement(self) -> str:
        n = len(set(self.clis))
        if n >= 3:
            return "unanimous"
        if n == 2:
            return "majority"
        return "solo"

    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "agreement": self.agreement,
            "agreeing_clis": sorted(set(self.clis)),
            "individual_findings": [
                {"cli": f.cli, "body": f.body} for f in self.findings
            ],
        }


def build_consensus(results: List[ReviewResult]) -> List[ConsensusFinding]:
    """Cluster findings by fingerprint across all CLI results."""
    clusters: Dict[str, ConsensusFinding] = defaultdict(ConsensusFinding)
    for res in results:
        for f in res.findings:
            fp = f.fingerprint()
            cf = clusters[fp]
            cf.fingerprint = fp
            cf.findings.append(f)
            cf.clis.append(f.cli)
    out = list(clusters.values())
    # Sort: unanimous > majority > solo, then critical > high > medium > low > info.
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    agreement_rank = {"unanimous": 0, "majority": 1, "solo": 2}
    out.sort(key=lambda c: (agreement_rank[c.agreement], severity_rank.get(c.severity, 5)))
    return out
