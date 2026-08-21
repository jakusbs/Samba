"""
applog.py — Samba (shared core)

Root-logger configuration: a rotating file plus a console handler.

Both applications call this from main().  Without it, every log.warning() in
hardware.py, server_sync.py and setup_lock.py goes nowhere, so there is no
post-mortem trail after a hardware problem — which is exactly when one is
needed.  Ported from Cryo, which had it; Samba_main did not.
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_CONFIGURED = False


def setup_logging(app_name: str = "samba",
                  log_dir: Optional[Path] = None,
                  max_bytes: int = 2 * 1024 * 1024,
                  backups: int = 5) -> Optional[Path]:
    """Configure the root logger with rotating file + console output.

    Log files live in ``<CONFIG_DIR>/logs/<app_name>.log`` (2 MB each, 5
    backups → ~10 MB max on disk), so a hardware problem can be diagnosed
    after the fact without flooding the disk.

    Idempotent: calling twice is a no-op, so an app that also has its own
    logging setup cannot end up with duplicated handlers.
    Returns the log path, or None if the file handler could not be created
    (a read-only home directory must not stop the application from starting).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return None
    _CONFIGURED = True

    if log_dir is None:
        try:
            from config import CONFIG_DIR
            log_dir = Path(CONFIG_DIR) / "logs"
        except Exception:
            log_dir = Path(os.path.expanduser("~/.config/moke_scan/logs"))

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    log_path: Optional[Path] = None
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{app_name}.log"
        fh = RotatingFileHandler(log_path, maxBytes=max_bytes,
                                 backupCount=backups, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception as exc:            # read-only home, permissions, full disk
        log_path = None
        logging.getLogger(__name__).warning(
            "Could not open log file in %s: %s", log_dir, exc)

    # Console handler — INFO and above only (keeps the terminal readable)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    if log_path is not None:
        logging.getLogger(__name__).info("Logging to %s", log_path)
    return log_path


def warn_stale_root_modules(app_dir: str, repo_root: str) -> list:
    """Warn about leftover *.py at the repo root that duplicate an app module.

    Both entry points put the repo root on sys.path so `import core` works.
    A stale module sitting there — a pre-core/ `hardware.py`, `config.py`,
    `plot_widgets.py`, … left behind by an old install — has the same import
    name as the app directory's re-export shim.  The entry points append the
    root rather than inserting it first, so the shim wins and the stale file
    is harmless; but its presence means the installation is a mix of old and
    new files, which is worth saying out loud once rather than discovering it
    through a confusing bug later.

    Returns the list of stale basenames found (empty when the tree is clean).
    """
    import glob
    log = logging.getLogger(__name__)
    try:
        app_mods = {os.path.basename(f)
                    for f in glob.glob(os.path.join(app_dir, "*.py"))}
        root_mods = {os.path.basename(f)
                     for f in glob.glob(os.path.join(repo_root, "*.py"))}
    except Exception:
        return []
    stale = sorted(app_mods & root_mods)
    if stale:
        log.warning(
            "Stale modules in the repo root shadow this app's own: %s. "
            "The repo only tracks test_runner.py there — these are left over "
            "from an older install and mean the tree is a mix of versions. "
            "Delete them from %s.", ", ".join(stale), repo_root)
    return stale
