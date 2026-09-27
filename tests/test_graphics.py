"""Real Qt/Matplotlib regressions; run separately from test_runner's stubs."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import threading
import time
import unittest
import numpy as np
import h5py
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QThread, QTimer
from core.plot_geometry import centers_to_edges, channel_style, physical_aspect
from core.plot_widgets import Live1DWidget, Live2DWidget
from core.plot_export import ExportOptions, prepare_export
from core.constants import X_NATURAL
from core.run_state import RunController, RunPhase
from core.data_browser import ScanFile, BrowserPlotWidget
from core.scan.runner import ScanRunner

APP = QApplication.instance() or QApplication([])


def process_until(predicate, timeout=2.):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(.005)
    return False


class GeometryTests(unittest.TestCase):
    def test_edges_preserve_centers_in_uniform_and_reversed_maps(self):
        np.testing.assert_array_equal(centers_to_edges([0, 1, 2]), [-.5, .5, 1.5, 2.5])
        np.testing.assert_array_equal(centers_to_edges([2, 1, 0]), [2.5, 1.5, .5, -.5])
        np.testing.assert_array_equal(centers_to_edges([2]), [1.5, 2.5])
        np.testing.assert_array_equal(centers_to_edges([0, 1, 4]), [-.5, .5, 2.5, 5.5])
        with self.assertRaises(ValueError):
            centers_to_edges([1, 1])

    def test_styles_are_identity_based_and_aspect_converts_units(self):
        sensor = {'device': 'lockin', 'attribute': 'x1', 'label': 'X', 'axis': 'Y1'}
        self.assertEqual(channel_style(sensor), channel_style(dict(sensor, axis='Y2')))
        self.assertEqual(physical_aspect('X (mm)', 'Y (µm)'), .001)
        self.assertIsNone(physical_aspect('Field (T)', 'Time (s)'))


class LivePlotTests(unittest.TestCase):
    def setUp(self):
        self.plot = Live1DWidget()
        self.sensors = [{'label': 'signal', 'unit': 'V', 'axis': 'Y1'}]
        self.plot.alloc(20, 'X', 'µm', self.sensors)
        self.plot.apply_config(self.sensors, X_NATURAL)

    def tearDown(self):
        self.plot._timer.stop(); self.plot._relayout_timer.stop()
        APP.processEvents()
        self.plot.close()

    def test_initial_follow_manual_zoom_hold_and_fit(self):
        for i in range(5):
            self.plot.update_point(i, i, {'signal': i * 2.})
        self.plot._throttled_draw()
        APP.processEvents()
        self.assertTrue(self.plot.follow_cb.isChecked())
        self.assertGreater(self.plot.ax1.get_xlim()[1], 4)
        self.plot.ax1.set_xlim(1, 2)
        self.plot.ax1.set_ylim(2, 4)
        self.assertFalse(self.plot.follow_cb.isChecked())
        self.plot.update_point(5, 10, {'signal': 50})
        self.plot._throttled_draw()
        np.testing.assert_allclose(self.plot.ax1.get_xlim(), [1, 2])
        np.testing.assert_allclose(self.plot.ax1.get_ylim(), [2, 4])
        self.plot.apply_config(self.sensors, X_NATURAL)
        np.testing.assert_allclose(self.plot.ax1.get_xlim(), [1, 2])
        self.plot._fit_view()
        self.assertTrue(self.plot.follow_cb.isChecked())
        self.assertGreater(self.plot.ax1.get_xlim()[1], 10)

    def test_export_physical_size_formats_and_source_unchanged(self):
        self.plot.update_point(0, 0, {'signal': 1})
        self.plot.update_point(1, 1, {'signal': 2})
        self.plot._fit_view()
        original_size = self.plot.fig.get_size_inches().copy()
        original_bg = self.plot.fig.get_facecolor()
        export, canvas = prepare_export(self.plot.fig, ExportOptions(85, 70, 150, 9, False, 'Sample A · revision test'))
        np.testing.assert_allclose(export.get_size_inches() * 25.4, [85, 70])
        self.assertFalse(export.axes[0].get_legend().get_visible())
        with tempfile.TemporaryDirectory() as folder:
            for ext in ('png', 'pdf', 'svg'):
                path = Path(folder) / ('figure.' + ext)
                export.savefig(path, dpi=150)
                self.assertGreater(path.stat().st_size, 500)
        np.testing.assert_allclose(self.plot.fig.get_size_inches(), original_size)
        self.assertEqual(self.plot.fig.get_facecolor(), original_bg)
        self.assertTrue(self.plot.ax1.get_legend().get_visible())

    def test_live_and_browser_maps_have_same_coordinates_and_missing_mask(self):
        live = Live2DWidget()
        live.set_sensor_units({'s': 'V'})
        live._throttled_draw()  # metadata may arrive before map allocation
        live.setup([0, 1, 2], [4, 2], 'X (µm)', 'Y (µm)', 's', 'viridis')
        live.update_point(1, 0, 3.)
        live._throttled_draw()
        data = np.array([[np.nan, 3., np.nan], [np.nan, np.nan, np.nan]])
        browser = BrowserPlotWidget()
        browser.plot_2d(data, [0, 1, 2], [4, 2], 'X (µm)', 'Y (µm)', 's')
        for axes in (live.ax, browser.ax):
            np.testing.assert_allclose(axes.get_xlim(), [-.5, 2.5])
            np.testing.assert_allclose(axes.get_ylim(), [5, 1])
            self.assertEqual(np.ma.count(axes.collections[0].get_array()), 1)
        live._set_color_limits(-5, 5)
        live.update_point(0, 0, 20)
        live._throttled_draw()
        self.assertEqual(live._img.get_clim(), (-5, 5))
        live.map_controls.physical.setChecked(True)
        self.assertEqual(live.ax.get_aspect(), 1.)
        live.clear(); live._timer.stop()
        APP.processEvents()
        live.close(); browser.close()


class DataTests(unittest.TestCase):
    def test_legacy_dataset_discovery_and_actionable_corrupt_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'legacy.h5'
            with h5py.File(path, 'w') as stream:
                stream.create_group('measurement')
                stream['measurement'].create_dataset('x', data=[0., 1., 2.])
                stream['measurement'].create_dataset('signal', data=[2., 4., 6.])
                stream.attrs['n_x'] = 3
                stream.attrs['n_y'] = 1
            scan = ScanFile(str(path))
            self.assertTrue(scan.valid, scan.error)
            self.assertEqual(scan.sensor_keys, ['signal'])
            result = scan.read_1d('auto', 'signal')
            self.assertIsNotNone(result)
            path.write_text('not HDF5')
            corrupt = ScanFile(str(path))
            self.assertFalse(corrupt.valid)
            self.assertTrue(corrupt.error)

    def test_active_run_owns_nested_config_snapshot(self):
        cfg = {'sensors': [{'label': 'original'}]}
        runner = ScanRunner(cfg, {'paths': ['first']})
        cfg['sensors'][0]['label'] = 'edited'
        self.assertEqual(runner.cfg['sensors'][0]['label'], 'original')

    def test_abort_interrupts_long_settle(self):
        runner = ScanRunner({}, {})
        stopped = threading.Event()
        worker = threading.Thread(target=lambda: (runner._sleep(30), stopped.set()))
        worker.start(); runner.abort()
        self.assertTrue(stopped.wait(1))
        worker.join()


class ShutdownTests(unittest.TestCase):
    def test_stopped_emits_only_after_real_thread_completion(self):
        class Writer(QThread):
            def __init__(self):
                super().__init__(); self.cancelled = threading.Event(); self.flush_allowed = threading.Event(); self.flushed = False
            def abort(self):
                self.cancelled.set()
            def run(self):
                self.cancelled.wait(2)
                self.flush_allowed.wait(2)
                self.flushed = True
        worker = Writer(); worker.start()
        controller = RunController()
        completed = []
        controller.stopped.connect(lambda: completed.append(worker.flushed and not worker.isRunning()))
        controller.stop_workers([worker])
        controller.set_phase(RunPhase.RUNNING)
        self.assertEqual(controller.phase, RunPhase.STOPPING)
        APP.processEvents()
        self.assertFalse(completed)
        worker.flush_allowed.set()
        self.assertTrue(process_until(lambda: bool(completed)))
        self.assertEqual(completed, [True])
        worker.wait()


if __name__ == '__main__':
    unittest.main()
