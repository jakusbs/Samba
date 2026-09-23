"""
scan_index.py — Samba

A persistent, incrementally-updated index of the HDF5 metadata in a data
directory, so the data browser can search months of measurements without
opening a single file while the operator is typing.

Why this exists
---------------
The browser used to read metadata straight off disk: opening every HDF5 in a
date folder the moment that folder was expanded — and the search box expands
every folder that matches.  One broad keystroke therefore opened the whole
archive on the GUI thread (measured: 3.5 s for 3697 files, 0.9 ms each, and it
grows every month).

The fix is the standard one: read each file's metadata **once ever**, keep it in
a small on-disk index, and search the index.  Re-syncing costs a stat walk of
the tree (measured: 6 ms for the same 3697 files) plus a real read of only the
files whose mtime or size changed — which is the running scan and nothing else.

The index is a cache, never a source of truth: deleting it costs one rebuild.
Nothing here writes to the data directory.

Qt-free and matplotlib-free on purpose (numpy is not needed either) so it can be
unit-tested in CI, like core/bd_fit.py and core/current_sweep.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import h5py

# Index format.  Bump when the stored fields change in a way that makes an
# existing file useless — a mismatch rebuilds from scratch rather than trying
# to migrate a cache that costs seconds to regenerate.
INDEX_VERSION = 3

# Metadata attributes worth searching on.  Deliberately a small subset: the
# index is loaded into memory in full, and these are the fields an operator
# actually types.  Everything else stays in the file and is shown by the
# metadata panel when a scan is selected.
SEARCH_FIELDS: Tuple[str, ...] = (
    "sample_id", "operator", "notes", "scan_name", "device_id",
    "incidence", "polarization", "scan_type",
)

# Columns the tree shows without opening anything.
_STATUS_FIELDS: Tuple[str, ...] = ("scan_status", "points_acquired", "points_planned")

_MAX_TEXT = 300          # truncate a pathological notes field
_PROGRESS_EVERY = 25     # files between progress callbacks
_SAVE_EVERY = 500        # files between intermediate saves during a long build


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _plain(v) -> str:
    """Coerce an HDF5 attribute to a short, JSON-safe string."""
    if v is None:
        return ""
    if isinstance(v, bytes):
        try:
            v = v.decode("utf-8", "replace")
        except Exception:
            return ""
    else:
        try:                      # numpy scalar → Python scalar
            v = v.item()
        except Exception:
            pass
        v = str(v)
    # HDF5 strings out of TANGO device buffers can carry NUL padding (§59).
    v = v.replace("\x00", "").strip()
    return v[:_MAX_TEXT]


def _as_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


# A scan's date is the name of the folder it sits in — the filename starts at
# HHMMSS and no metadata attribute carries it — so without this a date is the
# one thing the search cannot find.
_DATE_DIR_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
# A date typed with separators, in the folder's own year-month-day order.
# Deliberately strict: a looser pattern would rewrite ordinary numeric terms
# ("0.5" → "0-5") and break searching for them.
_DATE_TERM_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?$")


def date_tokens(path: str) -> str:
    """Searchable date text for a scan, from its date folder.

    Both the compact and dashed spellings, so plain substring matching covers
    the whole range of what an operator types: `20260608`, `2026-06-08`,
    `202606` or `2026-06` for the month, `2026` for the year.
    """
    parent = os.path.basename(os.path.dirname(os.path.abspath(path)))
    m = _DATE_DIR_RE.match(parent)
    if not m:
        return ""
    y, mo, d = m.groups()
    return f"{y}{mo}{d} {y}-{mo}-{d}"


def normalize_term(term: str) -> str:
    """Canonicalise a date-shaped search term; leave everything else alone.

    `2026/6/8` and `2026-6-8` both become `2026-06-08`, which is one of the
    spellings `date_tokens` stores — so the separator and zero-padding the
    operator happens to use stop mattering.
    """
    m = _DATE_TERM_RE.match(term)
    if not m:
        return term
    y, mo, d = m.group(1), m.group(2), m.group(3)
    out = f"{y}-{int(mo):02d}"
    if d is not None:
        out += f"-{int(d):02d}"
    return out


def index_path(save_dir: str, config_dir) -> str:
    """Where the index for `save_dir` lives.

    Keyed by the full path (hashed) so two setups — or the same setup pointed at
    a different disk — never share one index, while the readable prefix keeps
    the config directory browsable by a human.
    """
    full = os.path.abspath(os.path.expanduser(save_dir))
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(full.rstrip(os.sep))) or "data"
    digest = hashlib.sha1(full.encode("utf-8")).hexdigest()[:8]
    return os.path.join(str(config_dir), f"browser_index_{slug}_{digest}.json")


def scan_tree(base: str) -> Dict[str, Tuple[float, int]]:
    """`{path: (mtime, size)}` for every .h5 under base/<date>/.

    A stat walk only — no file is opened.  This is what makes a re-sync cheap.
    """
    found: Dict[str, Tuple[float, int]] = {}
    base = os.path.expanduser(base)
    try:
        date_dirs = list(os.scandir(base))
    except OSError:
        return found
    for d in date_dirs:
        try:
            if not d.is_dir():
                continue
            for e in os.scandir(d.path):
                if not e.name.lower().endswith(".h5"):
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                found[e.path] = (st.st_mtime, st.st_size)
        except OSError:
            continue          # unreadable folder — skip, don't take the walk down
    return found


def read_entry(path: str, mtime: float, size: int) -> Dict:
    """Read one file's searchable metadata.  Never raises.

    A file that cannot be read still gets an entry (`ok: False`) stamped with
    its mtime/size, so it is not re-opened on every sync — while a scan that is
    still being written keeps changing size and is therefore re-read until it
    is complete.
    """
    # The date comes from the path, so it is recorded even for a file that
    # cannot be opened — a broken scan stays findable by when it was taken.
    entry: Dict = {"mtime": mtime, "size": size, "ok": False,
                   "name": os.path.basename(path), "date": date_tokens(path)}
    try:
        with h5py.File(path, "r") as f:
            src = f.get("metadata", f)
            root = f.attrs
            for k in SEARCH_FIELDS:
                v = _plain(src.attrs.get(k, root.get(k)))
                if v:
                    entry[k] = v
            # scan_type and status live in the root attrs in both layouts
            st = _plain(root.get("scan_status", src.attrs.get("scan_status", "")))
            entry["scan_status"] = st or "completed"
            if not entry.get("scan_type"):
                entry["scan_type"] = _plain(root.get("scan_type", "?")) or "?"
            if _as_int(src.attrs.get("is_temp_sweep", 0)):
                entry["scan_type"] = "TEMP_SWEEP"
            is_dc = entry["scan_type"] == "DC_HYST"
            n_loop = _as_int(src.attrs.get("n_loop", 0))
            entry["points_acquired"] = _as_int(src.attrs.get("points_acquired", 0))
            entry["points_planned"] = _as_int(
                src.attrs.get("points_planned", n_loop if is_dc else 0))
            entry["ok"] = True
    except Exception:
        pass
    return entry


def entry_blob(entry: Dict) -> str:
    """The lowercase text a search matches against."""
    parts = [entry.get("name", ""), entry.get("date", "")]
    for k in SEARCH_FIELDS:
        v = entry.get(k)
        if v:
            parts.append(str(v))
    return " ".join(parts).lower()


def split_terms(text: str) -> List[str]:
    """Search terms, with date-shaped ones canonicalised (see normalize_term)."""
    return [normalize_term(t) for t in (text or "").lower().split() if t]


# ─────────────────────────────────────────────────────────────────────────────
# The index
# ─────────────────────────────────────────────────────────────────────────────
class ScanIndex:
    """Metadata cache for one data directory.

    Thread model: `sync()` is meant to run on a worker thread and is the only
    method that opens files.  `get()` / `blob()` / `matches()` are plain dict
    lookups and are safe to call from the GUI thread while a sync is running —
    the worker only ever adds keys, and CPython dict writes are atomic under the
    GIL, so a reader sees either the old entry or the new one, never a partial.
    """

    def __init__(self, base_dir: str, cache_file: Optional[str] = None):
        self.base_dir = base_dir
        self.cache_file = cache_file
        self.entries: Dict[str, Dict] = {}
        self._blobs: Dict[str, str] = {}
        self.loaded = False
        self.dirty = False

    # ── persistence ──────────────────────────────────────────────────────────
    def load(self) -> bool:
        """Read the cache from disk.  A missing/corrupt/outdated file is simply
        an empty index — it costs one rebuild, never an error."""
        self.loaded = True
        if not self.cache_file or not os.path.isfile(self.cache_file):
            return False
        try:
            with open(self.cache_file, "r", encoding="utf-8") as fh:
                blob = json.load(fh)
            if int(blob.get("version", 0)) != INDEX_VERSION:
                return False
            entries = blob.get("files", {})
            if not isinstance(entries, dict):
                return False
            self.entries = {k: v for k, v in entries.items() if isinstance(v, dict)}
            self._blobs = {k: entry_blob(v) for k, v in self.entries.items()}
            return True
        except Exception:
            self.entries = {}
            self._blobs = {}
            return False

    def save(self) -> bool:
        """Atomic write (temp file + os.replace), so a crash mid-save leaves the
        previous index intact rather than a truncated one."""
        if not self.cache_file or not self.dirty:
            return False
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            payload = {"version": INDEX_VERSION,
                       "base_dir": self.base_dir,
                       "files": self.entries}
            fd, tmp = tempfile.mkstemp(
                dir=os.path.dirname(self.cache_file), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.cache_file)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            self.dirty = False
            return True
        except Exception:
            return False

    # ── building ─────────────────────────────────────────────────────────────
    def stale(self, found: Dict[str, Tuple[float, int]]) -> List[str]:
        """Paths that must actually be opened: new, or changed since indexed."""
        out = []
        for fp, (mtime, size) in found.items():
            e = self.entries.get(fp)
            if e is None or e.get("size") != size or e.get("mtime") != mtime:
                out.append(fp)
        return out

    def sync(self,
             progress_cb: Optional[Callable[[int, int], None]] = None,
             should_stop: Optional[Callable[[], bool]] = None,
             save: bool = True) -> Dict:
        """Bring the index in line with the directory.  Runs on a worker thread.

        `should_stop` is polled between files so a panel being torn down (or a
        second sync starting) can cut this one short; whatever was read so far
        is kept and saved, because a partial index is still a faster start next
        time than none.
        """
        found = scan_tree(self.base_dir)

        # Drop entries for files that are gone, so the index cannot grow forever.
        removed = [fp for fp in self.entries if fp not in found]
        for fp in removed:
            self.entries.pop(fp, None)
            self._blobs.pop(fp, None)
        if removed:
            self.dirty = True

        todo = self.stale(found)
        total = len(todo)
        done = 0
        stopped = False
        for fp in todo:
            if should_stop is not None and should_stop():
                stopped = True
                break
            mtime, size = found[fp]
            entry = read_entry(fp, mtime, size)
            self.entries[fp] = entry
            self._blobs[fp] = entry_blob(entry)
            self.dirty = True
            done += 1
            if progress_cb is not None and (done % _PROGRESS_EVERY == 0 or done == total):
                try:
                    progress_cb(done, total)
                except Exception:
                    pass          # a broken observer must not kill the sync
            # Checkpoint a long first build, so quitting halfway through does
            # not throw away the work and start from zero next launch.
            if save and done % _SAVE_EVERY == 0:
                self.save()

        if save:
            self.save()
        return {"read": done, "total": total, "removed": len(removed),
                "indexed": len(self.entries), "stopped": stopped}

    # ── lookup ───────────────────────────────────────────────────────────────
    def get(self, path: str) -> Optional[Dict]:
        """The stored entry, or None if this file has never been indexed.

        An entry with `ok: False` is returned as-is — "we looked and could not
        read it" is a different answer from "we have not looked yet", and the
        browser shows them differently ("?" versus "…").
        """
        return self.entries.get(path)

    def blob(self, path: str) -> Optional[str]:
        return self._blobs.get(path)

    def matches(self, path: str, terms: Iterable[str], fallback_name: str = "") -> bool:
        """True when every term appears in this file's indexed text.

        Falls back to the filename for a file not indexed yet (first run, or a
        scan written seconds ago), so the search degrades to what it always did
        rather than hiding a file that exists.
        """
        terms = list(terms)
        if not terms:
            return True
        blob = self._blobs.get(path)
        if blob is None:
            # Not indexed yet: the name and the date are both free (the date is
            # the folder), so only the file's own metadata is missing.
            blob = " ".join((fallback_name or os.path.basename(path or ""),
                             date_tokens(path or ""))).lower()
        return all(t in blob for t in terms)
