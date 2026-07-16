"""
theme.py — central colour / style tokens for SAMBA.

Single home for the Catppuccin Mocha values that were previously copy-pasted
across the UI and plot modules, plus the plot-specific palettes.  New code
should import tokens from here; existing hard-coded hex values are migrated
opportunistically when their file is touched.

The curve palettes were validated for colour-vision-deficiency and
normal-vision separation between adjacent entries on the dark plot surface
(#12121f) — keep the ORDER, it is part of the validation.
"""

# ── Catppuccin Mocha (dark UI) ───────────────────────────────────────────────
MOCHA = {
    "rosewater": "#f5e0dc", "flamingo": "#f2cdcd", "pink":     "#f5c2e7",
    "mauve":     "#cba6f7", "red":      "#f38ba8", "maroon":   "#eba0ac",
    "peach":     "#fab387", "yellow":   "#f9e2af", "green":    "#a6e3a1",
    "teal":      "#94e2d5", "sky":      "#89dceb", "sapphire": "#74c7ec",
    "blue":      "#89b4fa", "lavender": "#b4befe",
    "text":      "#cdd6f4", "subtext0": "#a6adc8", "overlay0": "#6c7086",
    "surface1":  "#45475a", "surface0": "#313244",
    "base":      "#1e1e2e", "mantle":   "#181825", "crust":    "#11111b",
}

# Plot surfaces
PLOT_FIG_BG  = "#1e1e2e"   # figure background
PLOT_AX_BG   = "#12121f"   # axes background
PLOT_TICK    = "#aaaacc"   # tick / axis-label ink
PLOT_SPINE   = "#3a3a5c"

# ── Curve palettes (validated adjacency order — do not reshuffle) ───────────
# Y1 (left axis): cool colours.  blue↔green↔lavender pass all separation
# checks; teal as a 4th is acceptable with the legend present.
PLOT_LEFT_COLORS  = ['#89b4fa', '#a6e3a1', '#b4befe', '#94e2d5', '#89dceb']
# Y2 (right axis): warm colours.  red↔yellow↔mauve↔peach pass all checks.
PLOT_RIGHT_COLORS = ['#f38ba8', '#f9e2af', '#cba6f7', '#fab387', '#eba0ac']

# ── Diverging colormaps (signed data → colour range centred on zero) ────────
DIVERGING_CMAPS = {
    'RdBu_r', 'seismic', 'bwr', 'coolwarm', 'PuOr_r', 'RdYlBu_r',
    'Spectral_r', 'PiYG', 'BrBG', 'twilight', 'twilight_shifted',
    # non-reversed twins, in case a config carries them
    'RdBu', 'PuOr', 'RdYlBu', 'Spectral', 'PiYG_r', 'BrBG_r',
}

# ── Dark → light curve-colour mapping for figure export ─────────────────────
# Catppuccin Mocha (pastel, for dark surfaces) → Catppuccin Latte (saturated,
# readable on white).  Used by the light-mode figure export so pastel curves
# don't wash out on a white background.
MOCHA_TO_LATTE = {
    "#f5e0dc": "#dc8a78", "#f2cdcd": "#dd7878", "#f5c2e7": "#ea76cb",
    "#cba6f7": "#8839ef", "#f38ba8": "#d20f39", "#eba0ac": "#e64553",
    "#fab387": "#fe640b", "#f9e2af": "#df8e1d", "#a6e3a1": "#40a02b",
    "#94e2d5": "#179299", "#89dceb": "#04a5e5", "#74c7ec": "#209fb5",
    "#89b4fa": "#1e66f5", "#b4befe": "#7287fd",
}

# Light-export ink colours
LIGHT_INK       = "#1f1f28"   # primary text / spines on white
LIGHT_INK_SOFT  = "#4c4f69"   # secondary text


def light_color_for(color) -> str:
    """Map a dark-theme curve colour to its light-surface counterpart.

    Accepts anything matplotlib returns as a colour; unknown colours are
    passed through unchanged.
    """
    try:
        key = str(color).lower()
        return MOCHA_TO_LATTE.get(key, color)
    except Exception:
        return color
