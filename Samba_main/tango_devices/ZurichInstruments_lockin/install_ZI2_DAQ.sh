#!/bin/bash
# install_ZI2_DAQ.sh — Install the updated ZI2 Tango device server (dev30933)
# Uses ZI's native Data Acquisition Module for proper averaging.
#
# Usage:
#   1. Copy this script + the Python files to ~/tango-devices/ZI2_DAQ/
#   2. Run: bash install_ZI2_DAQ.sh
#   3. Register in Jive (see instructions at the end)
#   4. Start: ZI2_DAQ <instance_name>
#
# This installs alongside the existing ZI2 device — it does NOT replace it.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEVICE_NAME="ZI2_DAQ"

echo "========================================="
echo " Installing $DEVICE_NAME Tango Device"
echo "========================================="

# ── 1. Check that the required files exist ──────────────────────────
for f in ThreadZI2_DAQ.py ZI2.py; do
    if [ ! -f "$SCRIPT_DIR/$f" ]; then
        echo "ERROR: $f not found in $SCRIPT_DIR"
        echo "Please ensure ThreadZI2_DAQ.py and ZI2.py are present here."
        exit 1
    fi
done

# ── 2. Create the package directory ─────────────────────────────────
echo "Creating package structure..."
mkdir -p "$SCRIPT_DIR/$DEVICE_NAME"

# Copy the device server file as the main module
cp "$SCRIPT_DIR/ZI2.py"             "$SCRIPT_DIR/$DEVICE_NAME/${DEVICE_NAME}.py"
cp "$SCRIPT_DIR/ThreadZI2_DAQ.py"   "$SCRIPT_DIR/$DEVICE_NAME/ThreadZI2.py"

# Create __init__.py that imports main()
cat > "$SCRIPT_DIR/$DEVICE_NAME/__init__.py" << 'EOF'
from .ZI2_DAQ import main
from .ThreadZI2 import ThreadZI2
EOF

# Patch imports in the copied device server:
# - Convert top-level "from ThreadZI2 import ThreadZI2" to a relative import
sed -i 's/^from ThreadZI2 import ThreadZI2$/from .ThreadZI2 import ThreadZI2/' \
    "$SCRIPT_DIR/$DEVICE_NAME/${DEVICE_NAME}.py"

# ── 3. Create setup.py ──────────────────────────────────────────────
cat > "$SCRIPT_DIR/setup.py" << EOF
from setuptools import setup, find_packages

setup(
    name='tangods-${DEVICE_NAME}',
    version='3.0.0',
    description='ZI2 MFLI Tango device (dev30933) with DAQ module averaging',
    packages=['${DEVICE_NAME}'],
    entry_points={
        'console_scripts': [
            '${DEVICE_NAME} = ${DEVICE_NAME}:main',
        ],
    },
    install_requires=[
        'pytango',
        'numpy',
        'zhinst>=24,<26',  # pin within supported LabOne major versions (24.x or 25.x)
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
echo "     Class:    ZI2"
echo "     Device:   hpp-N42/measure/ZI2_DAQ  (or whatever you prefer)"
echo ""
echo "  2. Set device property:"
echo "     DeviceProxy = hpp-N42/socket/ZI2"
echo ""
echo "  3. Start the server:"
echo "     ${DEVICE_NAME} <instance>"
echo ""
echo "  4. Test in samba: change the device path in sensor config"
echo "     from hpp-N42/measure/ZI2 to hpp-N42/measure/ZI2_DAQ"
echo ""
echo "  The old ZI2 device remains untouched and can run in parallel."
