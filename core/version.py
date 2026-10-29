"""The application version — the one place it is written.

Read by Samba_main/config.py and Cryo/config.py (APP_VERSION: window titles,
`--version`, and the `samba_version` provenance attribute in every scan file),
and by pyproject.toml (`version = {attr = "core.version.__version__"}`).

Convention: bump the decimal part on every regular commit (13.38 → 13.39 → …),
the major part only for a release or a breaking change, and tag the commit
`v<version>`.  Independent of the scan-config SCHEMA_VERSION in config.py.
13.31 is deliberately skipped (a declined branch published itself as 13.31).
"""
__version__ = "13.42"
