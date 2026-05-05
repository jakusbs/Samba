#!/bin/bash
# install_ZI_DAQ.sh — Install the updated ZI Tango device server (dev4855)
# Uses ZI's native Data Acquisition Module for proper averaging.
#
# Usage:
#   1. Copy this script + the Python files to ~/tango-devices/ZI_DAQ/
#   2. Run: bash install_ZI_DAQ.sh
#   3. Register in Jive (see instructions at the end)
#   4. Start: ZI_DAQ <instance_name>
#
# This installs alongside the existing ZI device — it does NOT replace it.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEVICE_NAME="ZI_DAQ"

echo "========================================="
echo " Installing $DEVICE_NAME Tango Device"
echo "========================================="

# ── 1. Check that the required files exist ──────────────────────────
for f in ThreadZI_DAQ.py ZI.py; do
    if [ ! -f "$SCRIPT_DIR/$f" ]; then
        echo "ERROR: $f not found in $SCRIPT_DIR"
        echo "Please ensure ThreadZI_DAQ.py and ZI.py are present here."
        exit 1
    fi
done

# ── 2. Create the package directory ─────────────────────────────────
echo "Creating package structure..."
mkdir -p "$SCRIPT_DIR/$DEVICE_NAME"

# Copy the device server file as the main module
cp "$SCRIPT_DIR/ZI.py"            "$SCRIPT_DIR/$DEVICE_NAME/${DEVICE_NAME}.py"
cp "$SCRIPT_DIR/ThreadZI_DAQ.py"  "$SCRIPT_DIR/$DEVICE_NAME/ThreadZI.py"

# Create __init__.py that imports main()
cat > "$SCRIPT_DIR/$DEVICE_NAME/__init__.py" << 'EOF'
from .ZI_DAQ import main
from .ThreadZI import ThreadZI
EOF

# Patch imports in the copied device server:
# - Change "from ThreadZI import*" to relative import
sed -i 's/from ThreadZI import\*/from .ThreadZI import ThreadZI/' \
    "$SCRIPT_DIR/$DEVICE_NAME/${DEVICE_NAME}.py"

# Patch the thread reference: ThreadZI(self) stays the same (class name unchanged)

# ── 3. Create setup.py ──────────────────────────────────────────────
cat > "$SCRIPT_DIR/setup.py" << EOF
from setuptools import setup, find_packages

setup(
    name='tangods-${DEVICE_NAME}',
    version='3.0.0',
    description='ZI MFLI Tango device (dev4855) with DAQ module averaging',
    packages=['${DEVICE_NAME}'],
    entry_points={
        'console_scripts': [
            '${DEVICE_NAME} = ${DEVICE_NAME}:main',
        ],
    },
    install_requires=[
        'pytango',
        'numpy',
        'zhinst',
    ],
)
EOF

# ── 4. pip install ──────────────────────────────────────────────────
echo "Installing with pip..."
cd "$SCRIPT_DIR"
pip install . --force-reinstall --quiet

echo ""
echo "========================================="
echo " Installation complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo ""
echo "  1. Register in Jive:"
echo "     Server:   ${DEVICE_NAME}/<instance>"
echo "     Class:    ZI"
echo "     Device:   hpp-N42/measure/ZI_DAQ  (or whatever you prefer)"
echo ""
echo "  2. Set device property:"
echo "     DeviceProxy = hpp-N42/socket/ZI"
echo ""
echo "  3. Start the server:"
echo "     ${DEVICE_NAME} <instance>"
echo ""
echo "  4. Test in samba: change the device path in sensor config"
echo "     from hpp-N42/measure/ZI to hpp-N42/measure/ZI_DAQ"
echo ""
echo "  The old ZI device remains untouched and can run in parallel."
