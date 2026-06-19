#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# cf-cache-audit  —  Setup script for fresh Ubuntu machines
# ─────────────────────────────────────────────────────────────
# Usage:  bash setup.sh
# ─────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Colour

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

# ── 1. Check we're in the right directory ────────────────────
if [[ ! -f "pyproject.toml" ]]; then
    fail "Run this script from the cf-cache-audit project root (where pyproject.toml is)."
fi
ok "Project root detected."

# ── 2. Install system dependencies ──────────────────────────
info "Installing system packages (python3, python3-venv, python3-pip) …"
if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-venv python3-pip python3-dev >/dev/null 2>&1
    ok "System packages installed."
else
    warn "apt-get not found — skipping system package install. Make sure Python 3.12+ and python3-venv are installed."
fi

# ── 3. Verify Python version ────────────────────────────────
PYTHON=""
for candidate in python3.12 python3.13 python3; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [[ "$major" -ge 3 && "$minor" -ge 12 ]]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    fail "Python 3.12+ is required but not found. Install it with:
    sudo apt install python3.12 python3.12-venv"
fi
ok "Using $PYTHON (version $($PYTHON --version 2>&1))"

# ── 4. Create virtual environment ───────────────────────────
if [[ -d ".venv" ]]; then
    warn "Virtual environment .venv/ already exists — reusing it."
else
    info "Creating virtual environment …"
    "$PYTHON" -m venv .venv
    ok "Virtual environment created at .venv/"
fi

# ── 5. Activate venv ────────────────────────────────────────
# shellcheck disable=SC1091
source .venv/bin/activate
ok "Virtual environment activated."

# ── 6. Upgrade pip ──────────────────────────────────────────
info "Upgrading pip …"
pip install --upgrade pip --quiet
ok "pip upgraded to $(pip --version | awk '{print $2}')"

# ── 7. Install the project ─────────────────────────────────
info "Installing cf-cache-audit and all dependencies …"
pip install -e ".[dev]" --quiet
ok "All dependencies installed."

# ── 8. Verify installation ──────────────────────────────────
info "Verifying installation …"
if cf-cache-audit --version &>/dev/null; then
    ok "cf-cache-audit $(cf-cache-audit --version 2>&1) is ready!"
else
    fail "Installation verification failed."
fi

# ── 9. Run tests ────────────────────────────────────────────
info "Running tests …"
pytest -q --tb=short 2>&1 | tail -3
ok "Tests complete."

echo ""
echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Setup complete!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
echo ""
echo "  To use the tool:"
echo ""
echo "    1. Activate the virtual environment:"
echo -e "       ${CYAN}source .venv/bin/activate${NC}"
echo ""
echo "    2. Run a scan:"
echo -e "       ${CYAN}cf-cache-audit https://example.com${NC}"
echo ""
echo "    3. Export to Excel:"
echo -e "       ${CYAN}cf-cache-audit https://example.com --xlsx report.xlsx${NC}"
echo ""
