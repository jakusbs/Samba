from core.play_intro import *  # noqa: F401,F403
import os as _os
from core.play_intro import show_splash as _core_show_splash

_HERE = _os.path.dirname(_os.path.abspath(__file__))


def show_splash(app, asset_dir=_HERE):
    """Wrapper that supplies the Cryo asset directory by default."""
    return _core_show_splash(app, asset_dir=asset_dir)
