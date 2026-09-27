"""NAS transfer worker — runs as a child process of core/server_sync.py.

Why a separate process: a GVFS/FUSE SMB call can block forever inside a kernel
syscall when the share stalls, and a thread stuck there cannot be cancelled.
The parent runs this with a timeout and SIGKILLs it, so the GUI never wedges.

What it fixes relative to the previous inline worker, which skipped a file
whenever the destination had the same byte count:

  * A scan file revised in place at the same size was silently never uploaded.
    Content is now identified by a digest of the source, remembered in a small
    index at the destination.
  * A file still being written (an in-progress scan) was uploaded mid-write.
    HDF5 files whose `scan_status` is "running" — or which cannot be opened
    because the writer holds the lock — are deferred to the next sync.

Cost discipline, because the link is slow (measured ~6 MB/s and ~26 ms per
file on the lab's SMB mount, against a local disk at ~1 GB/s):

  * The steady-state path is stat-only.  A file whose source and destination
    signatures both match the index is skipped without opening anything.
  * Only the SOURCE is digested, never the destination.  Hashing the remote
    side would mean re-reading the whole archive over SMB — ~250 s for the
    current 1.1 GB Green directory, far past the parent's kill timeout, so
    the first sync would be killed before writing its index and would never
    make progress.
  * On a first run (or a lost index) an existing destination file is adopted
    on size + mtime rather than by hashing it.  copyfile does not preserve
    mtime, so the destination's mtime is its upload time: a source newer than
    its destination changed after it was uploaded and is re-copied.
  * A soft deadline stops the run cleanly and saves the index, so a large
    backlog makes progress across several syncs instead of being killed
    part-way every time.

Copies are published atomically (sidecar + os.replace) and size-verified.
copyfile, not copy2: SMB mounts reject the utime() that copy2 performs after
copying.  Full read-back verification is deliberately not done — it would
halve throughput on an already slow link, and truncation (the realistic
failure) is caught by the size check.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

INDEX_NAME = ".samba-sync-index.json"
_HDF5_SUFFIXES = {".h5", ".hdf5", ".nxs"}
# Stay below the parent's kill timeout so the index is always saved.
DEFAULT_BUDGET_S = 45.0
# SMB timestamp granularity is coarse; only a clearly newer source counts.
_MTIME_SLACK_S = 3.0
# Persist the index periodically so a kill cannot discard all the bookkeeping.
_CHECKPOINT_EVERY = 200


def _signature(path: Path):
    st = path.stat()
    return [st.st_size, st.st_mtime_ns]


def _digest(path: Path) -> str:
    h = hashlib.blake2b(digest_size=32)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _finalized(path: Path) -> bool:
    """True when the file is complete and safe to publish.

    Checked only for files we are about to copy, so the HDF5 open cost is
    paid for new/changed files rather than for the whole archive.
    """
    if path.suffix.lower() not in _HDF5_SUFFIXES:
        return True
    try:
        import h5py
    except ImportError:
        return True
    try:
        with h5py.File(path, "r") as f:
            status = f.attrs.get("scan_status", "completed")
            if isinstance(status, bytes):
                status = status.decode("utf-8", errors="replace")
            return str(status) != "running"
    except OSError:
        # The writer holds the HDF5 lock — the scan is still going.
        return False


def _write_index(destination: Path, data: dict) -> None:
    try:
        fd, tmp = tempfile.mkstemp(prefix=".samba-index-", suffix=".part",
                                   dir=str(destination))
    except OSError:
        return
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        os.replace(tmp, str(destination / INDEX_NAME))
    except OSError:
        Path(tmp).unlink(missing_ok=True)


def _read_index(destination: Path) -> dict:
    try:
        data = json.loads((destination / INDEX_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _collect(dirs: list, files: list) -> dict:
    """Group every (source, relative-path) pair by destination directory."""
    groups: dict = {}
    for entry in dirs:
        source, destination = Path(entry["src"]), Path(entry["dst"])
        if not source.is_dir():
            continue
        try:
            if destination.resolve().is_relative_to(source.resolve()):
                raise ValueError("sync destination must not be inside the source")
        except (OSError, AttributeError):
            pass
        for path in sorted(source.rglob("*")):
            if (path.is_file() and not path.name.endswith(".part")
                    and path.name != INDEX_NAME):
                groups.setdefault(destination, []).append(
                    (path, path.relative_to(source)))
    for entry in files:
        source = Path(entry["src"])
        if source.is_file():
            groups.setdefault(Path(entry["dst"]), []).append(
                (source, Path(source.name)))
    return groups


def _needs_copy(source: Path, target: Path, cached: dict,
                src_sig: list, result: dict):
    """Decide what to do with one file.  Returns (action, digest_or_None).

    action is 'skip', 'copy' or 'adopt'.  Only the source is ever hashed.
    """
    if not target.is_file():
        return "copy", None

    # Fast path: both sides exactly as the index last recorded them.
    if (cached.get("source") == src_sig
            and cached.get("source_path") == str(source)):
        try:
            if cached.get("destination") == _signature(target):
                return "skip", None
        except OSError:
            return "copy", None

    if not cached:
        # First run, or the index was lost.  Adopt on cheap metadata rather
        # than reading the whole destination back over SMB.
        try:
            dst_st = target.stat()
        except OSError:
            return "copy", None
        if dst_st.st_size != src_sig[0]:
            return "copy", None
        if source.stat().st_mtime > dst_st.st_mtime + _MTIME_SLACK_S:
            # copyfile does not preserve mtime, so the destination's mtime is
            # when it was uploaded: a newer source changed after that.
            return "copy", None
        return "adopt", None

    # The index knows this file but the source moved on — compare content.
    digest = _digest(source)
    if digest == cached.get("digest"):
        return "skip", digest
    return "copy", digest


def sync_paths(dirs: list, files: list, budget_s: float = DEFAULT_BUDGET_S) -> dict:
    started = time.monotonic()
    result = {"ok": True, "copied": 0, "skipped": 0, "adopted": 0,
              "deferred": 0, "remaining": 0, "errors": [], "log": []}

    for destination, entries in _collect(dirs, files).items():
        try:
            destination.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            result["errors"].append(f"{destination}: {exc}")
            continue
        index = _read_index(destination)
        dirty = 0

        for source, relative in entries:
            if time.monotonic() - started > budget_s:
                # Out of time: leave the rest for the next sync rather than
                # being killed mid-file with nothing recorded.
                result["remaining"] += 1
                continue
            target = destination / relative
            key = relative.as_posix()
            tmp = None
            try:
                src_sig = _signature(source)
                action, digest = _needs_copy(
                    source, target, index.get(key, {}), src_sig, result)

                if action == "skip":
                    result["skipped"] += 1
                    if digest is not None:
                        index[key] = {"source": src_sig,
                                      "source_path": str(source),
                                      "destination": _signature(target),
                                      "digest": digest}
                        dirty += 1
                    continue

                if action == "adopt":
                    result["adopted"] += 1
                    index[key] = {"source": src_sig, "source_path": str(source),
                                  "destination": _signature(target),
                                  "digest": None}
                    dirty += 1
                    continue

                if not _finalized(source):
                    result["deferred"] += 1
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.",
                                           suffix=".part", dir=str(target.parent))
                os.close(fd)
                shutil.copyfile(str(source), tmp)

                # Truncation check, and confirm the source held still.
                if os.path.getsize(tmp) != src_sig[0] or _signature(source) != src_sig:
                    result["deferred"] += 1
                    continue
                os.replace(tmp, str(target))
                tmp = None
                result["copied"] += 1
                index[key] = {"source": src_sig, "source_path": str(source),
                              "destination": _signature(target),
                              "digest": digest if digest is not None else _digest(source)}
                dirty += 1
                if dirty >= _CHECKPOINT_EVERY:
                    _write_index(destination, index)
                    dirty = 0
            except (OSError, ValueError) as exc:
                result["errors"].append(f"{source}: {exc}")
            finally:
                if tmp:
                    Path(tmp).unlink(missing_ok=True)

        if dirty:
            _write_index(destination, index)

    parts = [f"{result['copied']} copied", f"{result['skipped']} unchanged"]
    if result["adopted"]:
        parts.append(f"{result['adopted']} already present")
    if result["deferred"]:
        parts.append(f"{result['deferred']} still being written (deferred)")
    if result["remaining"]:
        parts.append(f"{result['remaining']} left for the next sync "
                     f"(time budget {budget_s:.0f}s)")
    result["log"].append(", ".join(parts))
    result["log"].extend(result["errors"])
    result["ok"] = not result["errors"] and not result["remaining"]
    return result


if __name__ == "__main__":
    payload = json.loads(sys.argv[1])
    print(json.dumps(sync_paths(payload.get("dirs", []),
                                payload.get("files", []),
                                float(payload.get("budget_s", DEFAULT_BUDGET_S)))))
