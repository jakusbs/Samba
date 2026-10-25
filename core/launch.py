"""Console entry points — `samba` and `samba-cryo` (see pyproject.toml).

Each application is a script run from its own directory (`python samba.py`)
with bare-name imports of its modules (`config`, `panels`, the `core`
re-export shims), so an installed entry point has to recreate exactly that:
the app directory first on sys.path, the repository root appended so that
`import core` resolves (never first — §74), and the working directory in the
app folder.  Nothing in the applications themselves changes; `python
samba.py` keeps working as before.
"""
import importlib
import os
import sys

from .version import __version__

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _launch(app_dir: str, module: str, name: str):
    if "--version" in sys.argv[1:]:
        print(f"{name} {__version__}")
        return 0
    d = os.path.join(_ROOT, app_dir)
    if not os.path.isdir(d):
        raise SystemExit(f"{name}: application directory not found: {d}")
    sys.path.insert(0, d)
    if _ROOT not in sys.path:
        sys.path.append(_ROOT)
    os.chdir(d)
    return importlib.import_module(module).main()


def main_samba():
    """`samba` — the Green / IR application (Samba_main/samba.py)."""
    return _launch("Samba_main", "samba", "Samba")


def main_cryo():
    """`samba-cryo` — the Cryo application (Cryo/samba_cryo.py)."""
    return _launch("Cryo", "samba_cryo", "Samba Cryo")
