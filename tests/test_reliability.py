import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Samba_main"))
from core.persistence import atomic_write_json
from core.sync_worker import sync_paths


class PersistenceTests(unittest.TestCase):
    def test_serialization_failure_preserves_last_valid_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "setup.json"
            atomic_write_json(path, {"sample": "keep"})
            with self.assertRaises(TypeError):
                atomic_write_json(path, {"sample": object()})
            self.assertEqual(json.loads(path.read_text()), {"sample": "keep"})

    def test_replace_failure_preserves_file_and_cleans_sidecar(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "setup.json"
            atomic_write_json(path, {"sample": "keep"})
            with patch("core.persistence.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    atomic_write_json(path, {"sample": "new"})
            self.assertEqual(json.loads(path.read_text()), {"sample": "keep"})
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_both_apps_back_up_corrupt_configuration(self):
        for app, setup in [("Samba_main", "Green"), ("Cryo", "Cryo")]:
            with self.subTest(app=app), tempfile.TemporaryDirectory() as folder:
                spec = importlib.util.spec_from_file_location(f"config_{app}", ROOT / app / "config.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.CONFIG_DIR = Path(folder)
                path = Path(folder) / f"{setup}.json"
                path.write_text("{ corrupt")
                loaded = module.load_setup(setup)
                self.assertTrue(loaded["_load_status"].startswith("error"))
                self.assertEqual(path.with_suffix(".json.bad").read_text(), "{ corrupt")
                module.save_setup(setup, loaded)
                self.assertIn("configs", json.loads(path.read_text()))


class SyncTests(unittest.TestCase):
    def test_same_size_hdf5_revision_is_published(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source"; source.mkdir()
            target = Path(folder) / "target"
            path = source / "scan.h5"
            with h5py.File(path, "w") as stream:
                stream.create_dataset("signal", data=[0., 0., 0.])
            dirs = [{"src": str(source), "dst": str(target)}]
            self.assertTrue(sync_paths(dirs, [])["ok"])
            size = path.stat().st_size
            with h5py.File(path, "r+") as stream:
                stream["signal"][:] = [4., 5., 6.]
            self.assertEqual(path.stat().st_size, size)
            self.assertTrue(sync_paths(dirs, [])["ok"])
            with h5py.File(target / "scan.h5") as stream:
                np.testing.assert_array_equal(stream["signal"][:], [4., 5., 6.])
            self.assertEqual(sync_paths(dirs, [])["skipped"], 1)

    def test_running_file_deferred_then_final_file_copied(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source"; source.mkdir()
            target = Path(folder) / "target"
            path = source / "scan.h5"
            with h5py.File(path, "w") as stream:
                stream.attrs["scan_status"] = "running"
            dirs = [{"src": str(source), "dst": str(target)}]
            result = sync_paths(dirs, [])
            self.assertEqual(result["pending"], 1)
            self.assertFalse(result["ok"])
            self.assertFalse((target / "scan.h5").exists())
            with h5py.File(path, "r+") as stream:
                stream.attrs["scan_status"] = "completed"
            self.assertTrue(sync_paths(dirs, [])["ok"])
            self.assertTrue((target / "scan.h5").exists())

    def test_failed_copy_does_not_replace_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source"; source.mkdir()
            target = Path(folder) / "target"; target.mkdir()
            (source / "scan.txt").write_text("new")
            (target / "scan.txt").write_text("old")
            with patch("core.sync_worker.shutil.copyfile", side_effect=OSError("offline")):
                result = sync_paths([{"src": str(source), "dst": str(target)}], [])
            self.assertFalse(result["ok"])
            self.assertEqual((target / "scan.txt").read_text(), "old")
            self.assertFalse(list(target.glob("*.part")))


if __name__ == "__main__":
    unittest.main()
