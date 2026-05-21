"""
server_sync.py — Non-blocking post-scan rsync to NAS.

Syncs three things for the active setup:
  ~/Data_Samba_<Setup>/      → <server_sync_dir>/Data_Samba_<Setup>/
  ~/ScanLists_<Setup>/       → <server_sync_dir>/ScanLists_<Setup>/
  ~/moke_data/lab_notebook_<Setup>.csv  → <server_sync_dir>/

All rsync calls run in a daemon thread so the UI is never blocked.
If the NAS is not mounted the calls fail silently (logged at WARNING level).

Usage in app code:
    from server_sync import sync_setup
    sync_setup(setup_name, setup_dict, done_cb=lambda ok: ...)
"""

import logging
import os
import shutil
import subprocess
import threading

log = logging.getLogger(__name__)


def _rsync(src: str, dst: str) -> bool:
    """rsync -av --mkpath src dst.  Returns True on success."""
    if not shutil.which("rsync"):
        log.warning("server_sync: rsync not found — install rsync to enable sync")
        return False
    src = src.rstrip(os.sep)
    if not os.path.exists(src):
        log.debug("server_sync: source missing, skipping: %s", src)
        return True  # nothing to sync yet, not an error
    try:
        r = subprocess.run(
            ["rsync", "-av", "--mkpath", src + os.sep, dst],
            capture_output=True, text=True, timeout=180,
        )
        if r.returncode == 0:
            log.info("server_sync OK: %s → %s", src, dst)
            return True
        log.warning("server_sync failed (code %d): %s", r.returncode,
                    (r.stderr or r.stdout)[:300])
        return False
    except subprocess.TimeoutExpired:
        log.warning("server_sync timed out: %s → %s", src, dst)
        return False
    except Exception as exc:
        log.warning("server_sync error: %s", exc)
        return False


def _rsync_file(src: str, dst_dir: str) -> bool:
    """Sync a single file to a directory on the server."""
    if not shutil.which("rsync"):
        return False
    src = os.path.expanduser(src)
    if not os.path.isfile(src):
        log.debug("server_sync: notebook missing, skipping: %s", src)
        return True
    try:
        r = subprocess.run(
            ["rsync", "-av", "--mkpath", src, dst_dir],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0:
            log.info("server_sync notebook OK: %s → %s", src, dst_dir)
            return True
        log.warning("server_sync notebook failed (code %d): %s",
                    r.returncode, (r.stderr or r.stdout)[:200])
        return False
    except Exception as exc:
        log.warning("server_sync notebook error: %s", exc)
        return False


def sync_setup(setup_name: str, setup: dict,
               done_cb=None) -> None:
    """Start a background sync for *setup_name*.

    Syncs data dir, ScanLists dir, and lab notebook CSV.
    done_cb(ok: bool) is called from the background thread when finished;
    wrap it with QTimer.singleShot(0, ...) on the Qt side if you need to
    update the GUI from it.

    Does nothing if server_sync_dir is empty or not set.
    """
    server_root = setup.get("server_sync_dir", "").strip().rstrip("/")
    if not server_root:
        return

    save_dir     = os.path.expanduser(setup.get("save_dir", ""))
    notebook_dir = os.path.expanduser(setup.get("notebook_dir", "~/moke_data"))
    parent       = os.path.dirname(save_dir.rstrip(os.sep))
    sl_dir       = os.path.join(parent, f"ScanLists_{setup_name}")

    data_dst     = f"{server_root}/Data_Samba_{setup_name}/"
    sl_dst       = f"{server_root}/ScanLists_{setup_name}/"
    nb_src       = os.path.join(notebook_dir, f"lab_notebook_{setup_name}.csv")
    nb_dst       = f"{server_root}/"

    def _run():
        log.info("server_sync: starting sync for %s", setup_name)
        ok  = _rsync(save_dir, data_dst)
        ok &= _rsync(sl_dir,   sl_dst)
        ok &= _rsync_file(nb_src, nb_dst)
        if done_cb:
            done_cb(ok)

    t = threading.Thread(target=_run, daemon=True, name=f"sync-{setup_name}")
    t.start()
