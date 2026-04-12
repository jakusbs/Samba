#!/bin/bash
# install_icon.sh — Install SAMBA icon for GNOME/Ubuntu taskbar
# Run from the SAMBA project directory:  bash install_icon.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ICON_PATH="$SCRIPT_DIR/samba_icon_256.png"

if [ ! -f "$ICON_PATH" ]; then
    echo "ERROR: samba_icon_256.png not found in $SCRIPT_DIR"
    exit 1
fi

echo "Installing SAMBA desktop entry..."
echo "  Project dir: $SCRIPT_DIR"
echo "  Icon path:   $ICON_PATH"

# Create .desktop file with absolute paths (most reliable on Ubuntu)
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/samba.desktop << EOF
[Desktop Entry]
Name=SAMBA
Comment=SAMBA v8 — ETH Zürich Intermag Lab
Exec=python3 $SCRIPT_DIR/samba.py
Icon=$ICON_PATH
Terminal=false
Type=Application
Categories=Science;Education;
StartupWMClass=samba
EOF

# Also copy to icon theme as fallback
mkdir -p ~/.local/share/icons/hicolor/256x256/apps
cp "$ICON_PATH" ~/.local/share/icons/hicolor/256x256/apps/samba.png
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor 2>/dev/null || true
update-desktop-database ~/.local/share/applications 2>/dev/null || true

echo ""
echo "Done! Now either:"
echo "  1. Log out and back in, OR"
echo "  2. Run:  killall gnome-shell 2>/dev/null; nohup gnome-shell --replace &"
echo ""
echo "Then restart Spyder and launch SAMBA."
