"""Installed entry points; retain the existing script launchers for lab PCs."""
import importlib
import sys
from pathlib import Path


def _launch(package, module):
    # The application-specific config modules and compatibility shims remain
    # isolated to one process. Shared core code uses qualified package imports.
    directory = Path(__file__).resolve().parents[1] / package
    sys.path.insert(0, str(directory))
    importlib.import_module(module).main()


def main():
    _launch("Samba_main", "samba")


def cryo():
    _launch("Cryo", "samba_cryo")
