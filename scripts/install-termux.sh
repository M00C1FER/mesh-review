#!/data/data/com.termux/files/usr/bin/bash
# mesh-review — Termux install script (Android arm64 / x86_64)
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/M00C1FER/mesh-review/main/scripts/install-termux.sh | bash
#   # or after cloning:
#   bash scripts/install-termux.sh
#
# What this does:
#   1. Installs Python 3.10+ and git via pkg (Termux package manager)
#   2. Clones or updates mesh-review into $HOME/.local/share/mesh-review
#   3. Creates a Python venv and installs mesh-review
#   4. Adds a launcher shim at $PREFIX/bin/mesh-review
#   5. Runs a smoke test (import + --list-clis)
#
# Termux notes:
#   - No systemd, no /etc/os-release — this script avoids both.
#   - The Bionic libc stack in Termux is compatible with CPython wheels from PyPI.
#   - LLM CLI tools (claude, gemini, copilot) must be separately installed as
#     npm / pip packages or custom scripts accessible on $PATH.
#   - For local falsification without a cloud key, install Ollama for Android
#     (or run Ollama on a PC on the same WiFi) and use the OpenAI-compatible
#     endpoint:
#       from mesh_review.review.falsify_sdk import make_openai_falsifier
#       falsifier = make_openai_falsifier(
#           model="qwen2.5-coder:7b",
#           base_url="http://<ollama-host>:11434/v1",
#           api_key="ollama",
#       )
#       gate = sigma_gate(consensus, falsifier=falsifier)
#
# Requirements:
#   - Termux (F-Droid build recommended; Google Play build may restrict network)
#   - Internet access for pkg install and pip install

set -euo pipefail

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
INSTALL_DIR="${HOME}/.local/share/mesh-review"
BIN_DIR="${PREFIX}/bin"

log()  { printf "\033[1m%s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*" >&2; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$*" >&2; exit 1; }

# ── Step 1: Termux packages ─────────────────────────────────────────────────
log "Step 1/4: Installing Termux packages (python, git, openssl)"
pkg update -y -q
pkg install -y -q python git openssl
ok "Termux packages ready"

# ── Step 2: Clone / update repo ─────────────────────────────────────────────
log "Step 2/4: Fetching mesh-review"
if [ -d "$INSTALL_DIR/.git" ]; then
    ( cd "$INSTALL_DIR" && git pull -q )
    ok "Updated $INSTALL_DIR"
else
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone -q https://github.com/M00C1FER/mesh-review.git "$INSTALL_DIR"
    ok "Cloned into $INSTALL_DIR"
fi

# ── Step 3: Python venv + install ───────────────────────────────────────────
log "Step 3/4: Installing mesh-review into venv"
cd "$INSTALL_DIR"
python -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e .
ok "mesh-review installed"

# ── Step 4: Launcher shim ───────────────────────────────────────────────────
cat > "$BIN_DIR/mesh-review" <<SHIM
#!/data/data/com.termux/files/usr/bin/bash
exec "$INSTALL_DIR/.venv/bin/mesh-review" "\$@"
SHIM
chmod +x "$BIN_DIR/mesh-review"
ok "Launcher → $BIN_DIR/mesh-review"

# ── Smoke test ───────────────────────────────────────────────────────────────
log "Step 4/4: Smoke test"
mesh-review --help >/dev/null && ok "--help OK"
mesh-review review --list-clis /dev/null 2>/dev/null | grep -q "claude" && ok "--list-clis OK" || warn "--list-clis: no CLIs registered (normal if no LLM CLIs installed)"

python - <<'PYSMOKE'
from mesh_review import ReviewConfig, run_review, build_consensus, sigma_gate
from mesh_review import SummaryConfig, run_summary, merge_structural
print("  ✓ Python API imports OK")
PYSMOKE

printf "\n\033[1;32mDone!\033[0m  Try:\n"
printf "  mesh-review review --list-clis dummy.py\n"
printf "\nTo use local Ollama as falsifier (PC on same WiFi):\n"
printf "  pip install openai\n"
printf "  # then in Python:\n"
printf "  # from mesh_review.review.falsify_sdk import make_openai_falsifier\n"
printf "  # falsifier = make_openai_falsifier(model='qwen2.5-coder:7b',\n"
printf "  #     base_url='http://<host>:11434/v1', api_key='ollama')\n"
