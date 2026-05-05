#!/bin/bash
# install_RTV40.sh — Install the RTV40 Tango device server
# Kentech RTV40 (/ RTV30) High-Voltage Pulse Generator, USB serial control.
#
# Usage:
#   1. Copy this script + RTV40_Pulser.py to ~/tango-devices/RTV40/
#   2. Run: bash install_RTV40.sh
#   3. Register in Jive (see instructions at the end)
#   4. Start: RTV40 <instance_name>

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEVICE_NAME="RTV40"

echo "========================================="
echo " Installing $DEVICE_NAME Tango Device"
echo "========================================="

# ── 1. Check that the required file exists ───────────────────────────
if [ ! -f "$SCRIPT_DIR/RTV40_Pulser.py" ]; then
    echo "ERROR: RTV40_Pulser.py not found in $SCRIPT_DIR"
    exit 1
fi

# ── 2. Create the package directory ─────────────────────────────────
echo "Creating package structure..."
mkdir -p "$SCRIPT_DIR/$DEVICE_NAME"

cp "$SCRIPT_DIR/RTV40_Pulser.py" "$SCRIPT_DIR/$DEVICE_NAME/${DEVICE_NAME}.py"

cat > "$SCRIPT_DIR/$DEVICE_NAME/__init__.py" << 'EOF'
from .RTV40 import main
EOF

# ── 3. Create setup.py ──────────────────────────────────────────────
cat > "$SCRIPT_DIR/setup.py" << EOF
from setuptools import setup, find_packages

setup(
    name='tangods-${DEVICE_NAME}',
    version='1.0.0',
    description='Kentech RTV40/RTV30 pulse generator Tango device server',
    packages=['${DEVICE_NAME}'],
    entry_points={
        'console_scripts': [
            '${DEVICE_NAME} = ${DEVICE_NAME}:main',
        ],
    },
    install_requires=[
        'pytango',
        'pyserial',
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
echo "     Class:    RTV40"
echo "     Device:   hpp-N42/pulser/RTV40  (or whatever you prefer)"
echo ""
echo "  2. Set device property:"
echo "     SerialPort = /dev/ttyUSB0  (or COMx on Windows)"
echo "     BaudRate   = 115200"
echo ""
echo "  3. Start the server:"
echo "     ${DEVICE_NAME} <instance>"
echo ""
echo "  4. In Jive, use the Connect command to open the serial port."
echo "     Use SendQuery to verify commands against the hardware manual."
echo "     Update CmdXxx device properties if any defaults don't match."
echo ""
echo "  NOTE: All command strings (CmdSetAmplitude, CmdGetWidth, etc.)"
echo "  are device properties — edit them in Jive without touching this file."
