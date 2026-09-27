"""Scientific plot geometry and stable channel styles shared by all views."""
import hashlib
import re
import numpy as np
from core.theme import PLOT_LEFT_COLORS, PLOT_RIGHT_COLORS, MOCHA


def centers_to_edges(values):
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or not values.size or not np.isfinite(values).all():
        raise ValueError("Map axes must be nonempty, finite one-dimensional arrays")
    if values.size == 1:
        return np.array([values[0] - .5, values[0] + .5])
    steps = np.diff(values)
    if not (np.all(steps > 0) or np.all(steps < 0)):
        raise ValueError("Map coordinates must be strictly monotonic")
    return np.r_[values[0] - steps[0] / 2,
                 values[:-1] + steps / 2, values[-1] + steps[-1] / 2]


def create_map(ax, data, x, y, cmap, **kwargs):
    """Use explicit cell edges, including reversed and nonuniform axes."""
    from matplotlib import colormaps
    colors = colormaps[cmap].copy()
    colors.set_bad(MOCHA["surface1"])
    xe, ye = centers_to_edges(x), centers_to_edges(y)
    artist = ax.pcolormesh(xe, ye, np.ma.masked_invalid(data), cmap=colors,
                          shading="flat", rasterized=True, **kwargs)
    ax.set_xlim(xe[0], xe[-1])
    ax.set_ylim(ye[0], ye[-1])
    return artist


def update_map(artist, data):
    artist.set_array(np.ma.masked_invalid(data))


def channel_style(sensor):
    """Identity-based color and line style; changing row order is harmless."""
    key = "|".join(str(sensor.get(k, "")) for k in ("device", "attribute", "label"))
    value = int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big")
    colors = PLOT_LEFT_COLORS + PLOT_RIGHT_COLORS
    return colors[value % len(colors)], ("-", "--", "-.", ":")[(value // len(colors)) % 4]


def physical_aspect(x_label, y_label):
    units = {"m": 1., "mm": 1e-3, "um": 1e-6, "µm": 1e-6,
             "μm": 1e-6, "nm": 1e-9, "pm": 1e-12}
    def unit(label):
        match = re.search(r"\(([^()]*)\)\s*$", label)
        return match.group(1) if match else ""
    xu, yu = unit(x_label), unit(y_label)
    if xu in units and yu in units:
        return units[yu] / units[xu]
    return 1. if xu == yu else None
