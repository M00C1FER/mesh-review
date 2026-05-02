# Reference Projects

Studied during the senior-dev review pass (2026-05-02). Each entry notes one
concrete pattern borrowed or consciously avoided in `mesh-review`.

---

## 1. `Aider-AI/aider` — Apache 2.0, ~44 k ★

<https://github.com/Aider-AI/aider>

**Pattern adopted**: *git-native, atomic commits per logical change.*  
Aider commits every AI-generated change as a standalone `git` commit with a
conventional message. This makes every incremental improvement independently
reviewable and trivially revertable. mesh-review follows the same discipline:
each improvement in this PR is one commit with a `feat:`/`fix:`/`docs:`/`ci:`
prefix and a body explaining *why*, not just *what*.

---

## 2. `Codium-ai/pr-agent` — Apache 2.0, ~7 k ★

<https://github.com/Codium-ai/pr-agent>

**Pattern adopted**: *structured JSON/TOML config drives tool behaviour; findings
are typed JSON objects, not free-text prose.*  
PR-Agent uses a `.pr_agent.toml` file and emits JSON-structured review
suggestions internally, making it easy to pipe output into CI pipelines,
wikis, or webhook handlers. mesh-review now validates each `Finding` against a
JSON Schema (`FINDING_SCHEMA`) before it is accepted, so bad LLM output is
surfaced as a parse warning rather than silently propagating garbage downstream.

---

## 3. `gitbutlerapp/gitbutler` — FSL-1.1 / BSL-1.1, ~20 k ★

<https://github.com/gitbutlerapp/gitbutler>

**Pattern noted (not adopted — different scope)**: *pre-push Butler Review.*  
GitButler runs an AI-powered review pass *before* the user pushes, catching
issues at the latest responsible moment inside the git workflow rather than
after a PR is opened. mesh-review's equivalent is the `--falsify` flag, which
runs the adversarial Sigma gate *before* any PR comment is posted. The
`pre-commit` hook added in this pass (`pre-commit-hooks.yaml`) mirrors the same
"catch it early" philosophy — mesh-review can run as a pre-commit check so
issues never even make it to push.

---

## 4. `continuedev/continue` — Apache 2.0, ~25 k ★

<https://github.com/continuedev/continue>

**Pattern adopted**: *checks-as-code in source control; model-agnostic.*  
Continue defines review checks as Markdown files in `.continue/checks/`, stored
in the repo alongside the code they protect. This keeps the "what to review for"
in version control rather than locked inside a SaaS dashboard. mesh-review's
`mesh-review.yaml` registry follows the same philosophy: the CLI list, timeouts,
and prompts live in the repo, are diffable, and travel with the code. The
pre-commit hook added in this pass furthers this by letting teams commit their
mesh-review config and have it enforced automatically on every developer machine.

---

## 5. `mozilla/mozreview` / `mozilla/star-chamber` — MPL 2.0, ~70 ★

<https://github.com/mozilla/star-chamber>

**Pattern noted**: *multi-reviewer consensus with explicit agreement tracking.*  
Mozilla's Star Chamber (and the older mozreview) required agreement from a
specific set of reviewers before a patch could land. mesh-review's consensus
layer (unanimous / majority / solo) echoes this: a finding that only one CLI
raises is tagged `solo` and skipped by `render_pr_comments`, just as a patch
with only one `r+` might still need a second reviewer. The Sigma falsification
gate is the adversarial counterpart — rather than waiting for a second `r+`, it
actively looks for a credible `r-`.

---

*Stars are approximate as of 2026-05-02.*
