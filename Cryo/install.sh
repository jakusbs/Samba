#!/bin/bash
# install.sh — SAMBA Cryo installer
# Installs system libraries, Python packages, and the desktop launcher.
#
# Usage:  sudo bash install.sh [conda_env]
#   conda_env  conda environment to install packages into (default: base)
# Example: sudo bash install.sh Tango

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ICON_PATH="$SCRIPT_DIR/samba_icon_256.png"
CONFIG_FILE="$SCRIPT_DIR/.install_config"

# Load saved env preference, then override with CLI arg if provided
SAVED_ENV=""
[ -f "$CONFIG_FILE" ] && SAVED_ENV=$(grep '^CONDA_ENV=' "$CONFIG_FILE" 2>/dev/null | cut -d= -f2)
CONDA_ENV="${1:-${SAVED_ENV:-base}}"

# Persist the choice for future runs
echo "CONDA_ENV=$CONDA_ENV" > "$CONFIG_FILE"

# Resolve real user when called with sudo
if [ -n "$SUDO_USER" ]; then
    REAL_USER="$SUDO_USER"
    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    REAL_USER="$USER"
    REAL_HOME="$HOME"
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC}  $1"; }
fail() { echo -e "  ${RED}✗${NC}  $1"; }
warn() { echo -e "  ${YELLOW}!${NC}  $1"; }
step() { echo -e "\n${BOLD}▶  $1${NC}"; }

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════╗${NC}"
echo -e "${BOLD}║      SAMBA Cryo  —  Installer        ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════╝${NC}"
echo "   Project dir : $SCRIPT_DIR"
echo "   Install user: $REAL_USER  (home: $REAL_HOME)"
echo "   Conda env   : $CONDA_ENV"

# ── 1. Check icon exists ──────────────────────────────────────────────────────
if [ ! -f "$ICON_PATH" ]; then
    fail "samba_icon_256.png not found in $SCRIPT_DIR"
    exit 1
fi

# ── 2. System packages ────────────────────────────────────────────────────────
step "System packages (apt)"
if command -v apt-get &>/dev/null; then
    SYS_PKGS="libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxkbcommon-x11-0"
    if [ "$(id -u)" -eq 0 ]; then
        apt-get install -y $SYS_PKGS 2>&1 \
            | grep -E "^(Get:|Inst |Setting up)" | sed 's/^/     /' || true
        ok "System packages installed"
    else
        warn "Not running as root — skipping system packages"
        warn "If Qt fails to start, run:  sudo apt install $SYS_PKGS"
    fi
else
    warn "apt-get not found — skipping (non-Debian system)"
fi

# ── 3. Find conda ─────────────────────────────────────────────────────────────
step "Conda"
CONDA_BASE=""
for candidate in \
    "$REAL_HOME/miniforge3" "$REAL_HOME/miniconda3" "$REAL_HOME/anaconda3" \
    "$HOME/miniforge3"      "$HOME/miniconda3"      "$HOME/anaconda3" \
    "/opt/miniforge3" "/opt/miniconda3" "/opt/conda"; do
    if [ -f "$candidate/etc/profile.d/conda.sh" ]; then
        CONDA_BASE="$candidate"; break
    fi
done

if [ -z "$CONDA_BASE" ]; then
    fail "conda not found — install miniforge/miniconda/anaconda first"
    exit 1
fi
ok "Found conda at $CONDA_BASE"

# ── 4. Create env if needed, install Python packages ─────────────────────────
step "Python packages  (env: $CONDA_ENV)"

INSTALL_CMD="
    source '$CONDA_BASE/etc/profile.d/conda.sh'

    # Create the env if it does not yet exist
    if ! conda env list 2>/dev/null | grep -qE '^${CONDA_ENV}[[:space:]]'; then
        echo '     Creating conda env ${CONDA_ENV} (python 3.11)...'
        conda create -y -q -n '${CONDA_ENV}' python=3.11
    fi

    conda activate '${CONDA_ENV}'

    # pytango is best installed from conda-forge
    echo '     Installing pytango from conda-forge...'
    conda install -y -q -c conda-forge pytango 2>&1 | tail -2 || true

    # Remaining packages via pip
    echo '     Installing numpy, matplotlib, h5py, PyQt6...'
    pip install -q numpy matplotlib h5py PyQt6
"

if [ -n "$SUDO_USER" ]; then
    sudo -u "$SUDO_USER" bash -c "$INSTALL_CMD"
else
    bash -c "$INSTALL_CMD"
fi
ok "Python packages installed"

# ── 5. Verify imports ─────────────────────────────────────────────────────────
step "Verifying imports"

VERIFY_CMD="
    source '$CONDA_BASE/etc/profile.d/conda.sh'
    conda activate '$CONDA_ENV'
    python3 - <<'PYEOF'
import importlib, sys
results = []
for pkg, import_name in [
        ('numpy',      'numpy'),
        ('matplotlib', 'matplotlib'),
        ('h5py',       'h5py'),
        ('PyQt6',      'PyQt6'),
        ('pytango',    'tango'),
]:
    try:
        m = importlib.import_module(import_name)
        ver = getattr(m, '__version__', 'ok')
        print(f'     \033[0;32m✓\033[0m  {pkg} {ver}')
    except ImportError as e:
        print(f'     \033[0;31m✗\033[0m  {pkg}  ({e})')
        sys.exit(1)
PYEOF
"

if [ -n "$SUDO_USER" ]; then
    sudo -u "$SUDO_USER" bash -c "$VERIFY_CMD"
else
    bash -c "$VERIFY_CMD"
fi
ok "All imports verified"

# ── 6. Desktop launcher ───────────────────────────────────────────────────────
step "Desktop launcher"

chmod +x "$SCRIPT_DIR/launch_samba.sh"

APPS_DIR="$REAL_HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
cat > "$APPS_DIR/samba_cryo.desktop" << EOF
[Desktop Entry]
Name=SAMBA Cryo
Comment=SAMBA Cryo — ETH Zürich Intermag Lab
Exec=bash $SCRIPT_DIR/launch_samba.sh $CONDA_ENV
Icon=$ICON_PATH
Terminal=false
Type=Application
Categories=Science;Education;
StartupWMClass=samba_cryo
EOF
chmod +x "$APPS_DIR/samba_cryo.desktop"
[ -n "$SUDO_USER" ] && chown "$REAL_USER":"$REAL_USER" "$APPS_DIR/samba_cryo.desktop"

ICONS_DIR="$REAL_HOME/.local/share/icons/hicolor/256x256/apps"
mkdir -p "$ICONS_DIR"
cp "$ICON_PATH" "$ICONS_DIR/samba_cryo.png"
[ -n "$SUDO_USER" ] && chown "$REAL_USER":"$REAL_USER" "$ICONS_DIR/samba_cryo.png"

gtk-update-icon-cache -f -t "$REAL_HOME/.local/share/icons/hicolor" 2>/dev/null || true
update-desktop-database "$APPS_DIR" 2>/dev/null || true
ok "Desktop entry created"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║   Installation complete!             ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════╝${NC}"
echo ""
echo "   Application menu : SAMBA Cryo"
echo "   Terminal launch  : bash $SCRIPT_DIR/launch_samba.sh $CONDA_ENV"
echo ""
