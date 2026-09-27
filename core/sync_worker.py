"""Bounded-process NAS transfer with revision tracking and atomic publication."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


def _signature(path):
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns]


def _digest(path):
    result = hashlib.blake2b(digest_size=32)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _finalized(path):
    if path.suffix.lower() not in {".h5", ".hdf5", ".nxs"}:
        return True
    import h5py
    try:
        with h5py.File(path, "r") as stream:
            status = stream.attrs.get("scan_status", "completed")
            if isinstance(status, bytes):
                status = status.decode("utf-8", errors="replace")
            return status != "running"
    except OSError:
        # HDF5's writer lock also catches active files without a status attr.
        return False


def _write_index(path, data):
    fd, name = tempfile.mkstemp(prefix=".samba-index-", suffix=".part",
                                dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def sync_paths(dirs, files):
    result = {"ok": True, "copied": 0, "skipped": 0, "pending": 0,
              "errors": [], "log": []}
    groups = {}
    for entry in dirs:
        source, destination = Path(entry["src"]), Path(entry["dst"])
        if not source.is_dir():
            continue
        if destination.resolve().is_relative_to(source.resolve()):
            raise ValueError("Sync destination must not be inside the source")
        groups.setdefault(destination, []).extend(
            (path, path.relative_to(source)) for path in sorted(source.rglob("*"))
            if path.is_file() and not path.name.endswith(".part")
            and path.name != ".samba-sync-index.json")
    for entry in files:
        source = Path(entry["src"])
        if source.is_file():
            groups.setdefault(Path(entry["dst"]), []).append((source, Path(source.name)))

    for destination, entries in groups.items():
        destination.mkdir(parents=True, exist_ok=True)
        index_path = destination / ".samba-sync-index.json"
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            if not isinstance(index, dict):
                index = {}
        except (OSError, ValueError):
            index = {}
        for source, relative in entries:
            target = destination / relative
            key = relative.as_posix()
            temporary = None
            try:
                if not _finalized(source):
                    result["pending"] += 1
                    continue
                before = _signature(source)
                cached = index.get(key, {})
                if (target.is_file() and cached.get("source") == before
                        and cached.get("destination") == _signature(target)
                        and cached.get("source_path") == str(source.resolve())):
                    result["skipped"] += 1
                    continue
                digest = _digest(source)
                if _signature(source) != before:
                    result["pending"] += 1
                    continue
                same = (target.is_file() and target.stat().st_size == before[0]
                        and _digest(target) == digest)
                if not same:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.",
                                                     suffix=".part", dir=target.parent)
                    os.close(fd)
                    shutil.copyfile(source, temporary)
                    # Do not publish a torn copy if another process changed it.
                    if _signature(source) != before or _digest(Path(temporary)) != digest:
                        result["pending"] += 1
                        continue
                    os.replace(temporary, target)
                    result["copied"] += 1
                else:
                    result["skipped"] += 1
                index[key] = {"source": before, "source_path": str(source.resolve()),
                              "destination": _signature(target), "digest": digest}
            except (OSError, ValueError) as exc:
                result["errors"].append(f"{source}: {exc}")
            finally:
                if temporary:
                    Path(temporary).unlink(missing_ok=True)
        _write_index(index_path, index)
    result["ok"] = not result["errors"] and not result["pending"]
    result["log"].append(
        f"{result['copied']} copied, {result['skipped']} unchanged, "
        f"{result['pending']} active/changing files deferred")
    result["log"].extend(result["errors"])
    return result


if __name__ == "__main__":
    payload = json.loads(sys.argv[1])
    print(json.dumps(sync_paths(payload.get("dirs", []), payload.get("files", []))))
