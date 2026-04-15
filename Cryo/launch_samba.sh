#!/bin/bash
# launch_samba.sh — Launch SAMBA Cryo with the correct conda Python.
# ──────────────────────────────────────────────────────────────────
# Set CONDA_ENV to whichever environment has the required packages.
# You can also override it at the command line:
#   bash launch_samba.sh Tango
# ──────────────────────────────────────────────────────────────────
CONDA_ENV="${1:-${SAMBA_CONDA_ENV:-base}}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Search for conda in order of preference
CONDA_BASE=""
for candidate in \
    "$HOME/miniforge3" \
    "$HOME/miniconda3" \
    "$HOME/anaconda3" \
    "$HOME/conda" \
    "/opt/miniforge3" \
    "/opt/miniconda3" \
    "/opt/conda" \
    "/usr/local/conda"; do
    if [ -f "$candidate/etc/profile.d/conda.sh" ]; then
        CONDA_BASE="$candidate"
        break
    fi
done

if [ -z "$CONDA_BASE" ]; then
    echo "ERROR: Could not find a conda installation."
    echo "Tried: ~/miniforge3, ~/miniconda3, ~/anaconda3, /opt/conda, ..."
    echo "Install conda or edit CONDA_BASE in launch_samba.sh manually."
    exit 1
fi

source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV"

echo "Using Python: $(which python3)  (env: $CONDA_ENV)"
cd "$SCRIPT_DIR"
python3 samba_cryo.py
