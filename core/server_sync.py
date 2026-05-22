"""
server_sync.py — Non-blocking post-scan file sync to NAS.

Syncs three things for the active setup:
  <local save_dir>/                     → <server_sync_dir>/Data_Samba_<Setup>/
  <parent of save_dir>/ScanLists_<Setup>/ → <server_sync_dir>/ScanLists_<Setup>/
  <notebook_dir>/lab_notebook_<Setup>.csv → <server_sync_dir>/

All copies run in a daemon thread so the UI is never blocked.
If the server path is not reachable the calls fail silently (logged at WARNING).

Uses shutil.copy2 + pathlib — no rsync dependency, works with GVFS mounts.

Usage:
    from server_sync import sync_setup
    sync_setup(setup_name, setup_dict, done_cb=lambda ok: ...)
"""

import logging
import os
import shutil
import threading
from pathlib import Path

log = logging.getLogger(__name__)


def _sync_dir(src: str, dst: str) -> bool:
    """Recursively copy new/changed files from src into dst.

    A file is considered changed when its size differs from the destination.
    Skips files that are already identical to avoid unnecessary NAS writes.
    Returns True on success (including when src doesn't exist yet).
    """
    src_p = Path(src)
    if not src_p.exists():
        log.debug("server_sync: source missing, skipping: %s", src)
        return True
    dst_p = Path(dst)
    try:
        dst_p.mkdir(parents=True, exist_ok=True)
        copied = skipped = 0
        for f in sorted(src_p.rglob("*")):
            if not f.is_file():
                continue
            rel      = f.relative_to(src_p)
            dst_file = dst_p / rel
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            if not dst_file.exists() or dst_file.stat().st_size != f.stat().st_size:
                shutil.copy2(f, dst_file)
                copied += 1
            else:
                skipped += 1
        log.info("server_sync OK: %s → %s  (%d copied, %d skipped)",
                 src, dst, copied, skipped)
        return True
    except Exception as exc:
        log.warning("server_sync error %s → %s: %s", src, dst, exc)
        return False


def _sync_file(src: str, dst_dir: str) -> bool:
    """Copy a single file to dst_dir if it is missing or has a different size."""
    src_p = Path(src)
    if not src_p.is_file():
        log.debug("server_sync: file missing, skipping: %s", src)
        return True
    try:
        dst_p = Path(dst_dir)
        dst_p.mkdir(parents=True, exist_ok=True)
        dst_file = dst_p / src_p.name
        if not dst_file.exists() or dst_file.stat().st_size != src_p.stat().st_size:
            shutil.copy2(src_p, dst_file)
            log.info("server_sync file OK: %s → %s", src, dst_dir)
        return True
    except Exception as exc:
        log.warning("server_sync file error %s → %s: %s", src, dst_dir, exc)
        return False


def sync_setup(setup_name: str, setup: dict, done_cb=None) -> None:
    """Start a background sync for *setup_name*.

    Syncs data dir, ScanLists dir, and lab notebook CSV to server_sync_dir.
    done_cb(ok: bool) is called from the background thread when finished;
    use QTimer.singleShot(0, ...) on the Qt side to marshal back to the GUI.

    Does nothing if server_sync_dir is empty or not set.
    """
    server_root = setup.get("server_sync_dir", "").strip().rstrip("/")
    if not server_root:
        return

    save_dir     = os.path.expanduser(setup.get("save_dir", ""))
    notebook_dir = os.path.expanduser(setup.get("notebook_dir", "~/moke_data"))
    parent       = os.path.dirname(save_dir.rstrip(os.sep))
    sl_dir       = os.path.join(parent, f"ScanLists_{setup_name}")

    data_dst = f"{server_root}/Data_Samba_{setup_name}"
    sl_dst   = f"{server_root}/ScanLists_{setup_name}"
    nb_src   = os.path.join(notebook_dir, f"lab_notebook_{setup_name}.csv")

    def _run():
        log.info("server_sync: starting sync for %s  root=%s", setup_name, server_root)
        ok  = _sync_dir(save_dir, data_dst)
        ok &= _sync_dir(sl_dir,   sl_dst)
        ok &= _sync_file(nb_src,  server_root)
        log.info("server_sync: finished for %s  ok=%s", setup_name, ok)
        if done_cb:
            done_cb(ok)

    t = threading.Thread(target=_run, daemon=True, name=f"sync-{setup_name}")
    t.start()
