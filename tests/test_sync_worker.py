"""Tests for the NAS sync worker (core/sync_worker.py).

Runs against real files in a temp directory — no NAS and no Qt needed.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import sync_worker as W  # noqa: E402

try:
    import h5py
    HAVE_H5PY = True
except ImportError:
    HAVE_H5PY = False


class SyncWorkerTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.src = root / "src"
        self.dst = root / "dst"
        self.src.mkdir()
        self.dst.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, **kw):
        return W.sync_paths([{"src": str(self.src), "dst": str(self.dst)}], [], **kw)

    def _write(self, name, data: bytes):
        p = self.src / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    # ── the bug this worker exists to fix ────────────────────────────────────

    def test_same_size_revision_is_uploaded(self):
        """A file rewritten in place at the same length must still be copied.

        The previous worker skipped on an equal byte count, so an HDF5 revised
        in place was silently never uploaded.
        """
        self._write("scan.dat", b"A" * 4096)
        self._run()
        self.assertEqual((self.dst / "scan.dat").read_bytes(), b"A" * 4096)

        self._write("scan.dat", b"B" * 4096)          # same size, new content
        result = self._run()

        self.assertEqual((self.dst / "scan.dat").read_bytes(), b"B" * 4096)
        self.assertEqual(result["copied"], 1)

    def test_unchanged_file_is_not_recopied(self):
        self._write("a.dat", b"x" * 100)
        first = self._run()
        self.assertEqual(first["copied"], 1)
        second = self._run()
        self.assertEqual(second["copied"], 0)
        self.assertEqual(second["skipped"], 1)

    def test_new_file_is_copied(self):
        self._write("a.dat", b"1")
        self._run()
        self._write("b.dat", b"2")
        result = self._run()
        self.assertEqual(result["copied"], 1)
        self.assertTrue((self.dst / "b.dat").is_file())

    def test_nested_directories_are_preserved(self):
        self._write("20260927/scan_001.dat", b"data")
        self._run()
        self.assertEqual((self.dst / "20260927" / "scan_001.dat").read_bytes(), b"data")

    # ── first run over an archive that is already on the server ─────────────

    def test_existing_destination_is_adopted_without_copying(self):
        """Pre-existing identical files must not be re-uploaded.

        Adoption is by size + mtime: hashing the destination would mean
        re-reading the whole archive over SMB.
        """
        self._write("old.dat", b"z" * 2048)
        (self.dst / "old.dat").write_bytes(b"z" * 2048)
        os.utime(self.dst / "old.dat", None)          # uploaded "now"
        result = self._run()
        self.assertEqual(result["copied"], 0)
        self.assertEqual(result["adopted"], 1)

    def test_source_newer_than_destination_is_recopied(self):
        """copyfile does not preserve mtime, so a newer source changed later."""
        self._write("old.dat", b"z" * 2048)
        (self.dst / "old.dat").write_bytes(b"q" * 2048)
        old = time.time() - 600
        os.utime(self.dst / "old.dat", (old, old))    # uploaded 10 min ago
        result = self._run()
        self.assertEqual(result["copied"], 1)
        self.assertEqual((self.dst / "old.dat").read_bytes(), b"z" * 2048)

    def test_different_size_destination_is_recopied(self):
        self._write("a.dat", b"y" * 500)
        (self.dst / "a.dat").write_bytes(b"y" * 10)
        result = self._run()
        self.assertEqual(result["copied"], 1)
        self.assertEqual((self.dst / "a.dat").read_bytes(), b"y" * 500)

    # ── files still being written ────────────────────────────────────────────

    @unittest.skipUnless(HAVE_H5PY, "h5py required")
    def test_running_scan_file_is_deferred(self):
        p = self.src / "live.h5"
        with h5py.File(p, "w") as f:
            f.attrs["scan_status"] = "running"
        result = self._run()
        self.assertEqual(result["deferred"], 1)
        self.assertFalse((self.dst / "live.h5").exists())
        self.assertFalse(result["ok"] is True and result["copied"] > 0)

    @unittest.skipUnless(HAVE_H5PY, "h5py required")
    def test_completed_scan_file_is_copied(self):
        p = self.src / "done.h5"
        with h5py.File(p, "w") as f:
            f.attrs["scan_status"] = "completed"
        result = self._run()
        self.assertEqual(result["copied"], 1)
        self.assertTrue((self.dst / "done.h5").is_file())

    @unittest.skipUnless(HAVE_H5PY, "h5py required")
    def test_file_open_for_writing_is_deferred(self):
        """No status attribute yet — the writer's lock must still defer it."""
        p = self.src / "opening.h5"
        handle = h5py.File(p, "w")
        try:
            result = self._run()
            self.assertEqual(result["deferred"], 1)
            self.assertFalse((self.dst / "opening.h5").exists())
        finally:
            handle.close()

    # ── hygiene ──────────────────────────────────────────────────────────────

    def test_partial_sidecars_are_never_published(self):
        self._write("real.dat", b"ok")
        (self.src / "stale.dat.part").write_bytes(b"torn")
        self._run()
        self.assertTrue((self.dst / "real.dat").is_file())
        self.assertFalse((self.dst / "stale.dat.part").exists())

    def test_index_is_not_itself_synced(self):
        self._write("a.dat", b"1")
        self._run()
        self._run()
        self.assertFalse((self.dst / W.INDEX_NAME / W.INDEX_NAME).exists())
        self.assertTrue((self.dst / W.INDEX_NAME).is_file())

    def test_lost_index_does_not_recopy_everything(self):
        for i in range(5):
            self._write(f"f{i}.dat", bytes([i]) * 64)
        self._run()
        (self.dst / W.INDEX_NAME).unlink()
        result = self._run()
        self.assertEqual(result["copied"], 0, "a lost index must not re-upload")
        self.assertEqual(result["adopted"], 5)

    def test_time_budget_stops_cleanly_and_resumes(self):
        for i in range(12):
            self._write(f"f{i:02d}.dat", bytes([i]) * 1024)
        first = W.sync_paths([{"src": str(self.src), "dst": str(self.dst)}], [],
                             budget_s=0.0)
        self.assertGreater(first["remaining"], 0)
        self.assertFalse(first["ok"], "a partial run must not report success")
        second = self._run()
        self.assertEqual(second["remaining"], 0)
        self.assertEqual(len(list(self.dst.glob("f*.dat"))), 12)

    def test_single_file_entry(self):
        nb = Path(self._tmp.name) / "lab_notebook_Green.csv"
        nb.write_text("a;b\n1;2\n")
        result = W.sync_paths([], [{"src": str(nb), "dst": str(self.dst)}])
        self.assertEqual(result["copied"], 1)
        self.assertEqual((self.dst / nb.name).read_text(), "a;b\n1;2\n")

    def test_notebook_growth_is_republished(self):
        nb = Path(self._tmp.name) / "lab_notebook_Green.csv"
        nb.write_text("a;b\n1;2\n")
        entry = [{"src": str(nb), "dst": str(self.dst)}]
        W.sync_paths([], entry)
        nb.write_text("a;b\n1;2\n3;4\n")
        result = W.sync_paths([], entry)
        self.assertEqual(result["copied"], 1)
        self.assertIn("3;4", (self.dst / nb.name).read_text())

    def test_missing_source_directory_is_not_an_error(self):
        result = W.sync_paths(
            [{"src": str(Path(self._tmp.name) / "nope"), "dst": str(self.dst)}], [])
        self.assertTrue(result["ok"])
        self.assertEqual(result["errors"], [])

    def test_runs_as_a_subprocess_and_emits_json(self):
        """The parent invokes this as a child process and parses stdout."""
        self._write("a.dat", b"hello")
        payload = json.dumps({"dirs": [{"src": str(self.src), "dst": str(self.dst)}],
                              "files": []})
        r = subprocess.run([sys.executable, W.__file__, payload],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        result = json.loads(r.stdout.strip())
        self.assertTrue(result["ok"])
        self.assertEqual(result["copied"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
