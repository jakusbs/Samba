"""
server_sync.py — Non-blocking post-scan file sync to NAS.

Copies three things for the active setup:
  <save_dir>/                       → <server_sync_dir>/Data_Samba_<Setup>/
  <parent of save_dir>/ScanLists_<Setup>/ → <server_sync_dir>/ScanLists_<Setup>/
  <notebook_dir>/lab_notebook_<Setup>.csv → <server_sync_dir>/

Each sync runs in a daemon thread.  The actual file I/O is done in a
*child process* (subprocess) with a hard timeout so that a hung GVFS/SMB
FUSE call can be killed rather than blocking the thread forever.

Usage:
    from server_sync import sync_setup
    sync_setup(setup_name, setup_dict, done_cb=lambda ok: ...)
"""

import json
import logging
import os
import subprocess
import sys
import threading

log = logging.getLogger(__name__)

_TIMEOUT_S = 60  # kill the copy process if it hangs longer than this

# Python source run inside the child process.
# sys.argv[1] is a JSON payload; stdout is a JSON result line.
_WORKER_SRC = r"""
import sys, shutil, json
from pathlib import Path

def sync_dir(src, dst):
    sp = Path(src)
    if not sp.exists():
        return 0, 0
    dp = Path(dst)
    dp.mkdir(parents=True, exist_ok=True)
    copied = skipped = 0
    for f in sorted(sp.rglob('*')):
        if not f.is_file():
            continue
        df = dp / f.relative_to(sp)
        df.parent.mkdir(parents=True, exist_ok=True)
        if not df.exists() or df.stat().st_size != f.stat().st_size:
            shutil.copyfile(str(f), str(df))
            copied += 1
        else:
            skipped += 1
    return copied, skipped

def sync_file(src, dst_dir):
    sp = Path(src)
    if not sp.is_file():
        return
    dp = Path(dst_dir)
    dp.mkdir(parents=True, exist_ok=True)
    df = dp / sp.name
    if not df.exists() or df.stat().st_size != sp.stat().st_size:
        shutil.copyfile(str(sp), str(df))

args  = json.loads(sys.argv[1])
lines = []
for d in args.get('dirs', []):
    c, s = sync_dir(d['src'], d['dst'])
    lines.append(f"{d['src']}: {c} copied, {s} skipped")
for f in args.get('files', []):
    sync_file(f['src'], f['dst'])
print(json.dumps({'ok': True, 'log': lines}))
"""


def _run_worker(dirs: list, files: list) -> bool:
    """Spawn a child process to do the actual file I/O.

    The child is killed after _TIMEOUT_S seconds so a hung GVFS call
    never permanently blocks the calling thread.
    """
    payload = json.dumps({'dirs': dirs, 'files': files})
    try:
        r = subprocess.run(
            [sys.executable, '-c', _WORKER_SRC, payload],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
        if r.returncode == 0:
            try:
                result = json.loads(r.stdout.strip())
                for line in result.get('log', []):
                    log.info('server_sync: %s', line)
            except Exception:
                pass
            return True
        log.warning('server_sync failed (exit %d): %s',
                    r.returncode, (r.stderr or r.stdout)[:400])
        return False
    except subprocess.TimeoutExpired:
        log.warning('server_sync: killed after %ds timeout — NAS unreachable?', _TIMEOUT_S)
        return False
    except Exception as exc:
        log.warning('server_sync error: %s', exc)
        return False


def sync_setup(setup_name: str, setup: dict, done_cb=None) -> None:
    """Start a background sync for *setup_name*.

    done_cb(ok: bool) is called from the background thread when finished;
    use QTimer.singleShot(0, ...) on the Qt side to marshal to the GUI thread.
    Does nothing if server_sync_dir is empty or not set.
    """
    server_root = setup.get('server_sync_dir', '').strip().rstrip('/')
    if not server_root:
        return

    save_dir     = os.path.expanduser(setup.get('save_dir', ''))
    notebook_dir = os.path.expanduser(setup.get('notebook_dir', '~/moke_data'))
    parent       = os.path.dirname(save_dir.rstrip(os.sep))
    sl_dir       = os.path.join(parent, f'ScanLists_{setup_name}')
    nb_src       = os.path.join(notebook_dir, f'lab_notebook_{setup_name}.csv')

    dirs  = [
        {'src': save_dir, 'dst': f'{server_root}/Data_Samba_{setup_name}'},
        {'src': sl_dir,   'dst': f'{server_root}/ScanLists_{setup_name}'},
    ]
    files = [{'src': nb_src, 'dst': server_root}]

    def _run():
        log.info('server_sync: starting  %s → %s', setup_name, server_root)
        ok = _run_worker(dirs, files)
        log.info('server_sync: done  %s  ok=%s', setup_name, ok)
        if done_cb:
            done_cb(ok)

    threading.Thread(target=_run, daemon=True, name=f'sync-{setup_name}').start()
