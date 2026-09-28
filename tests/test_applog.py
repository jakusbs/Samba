"""Third-party debug chatter must not flood the application log.

matplotlib's font_manager scores every installed font on a lookup miss.  With
the root logger at DEBUG that reached the log file: 41 602 of 41 672 recorded
lines on the lab machine, and three plot draws were enough to fill a 2 MB file
and rotate a startup line out of the live log within a second.
"""
import logging
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import applog  # noqa: E402


class QuietNoisyLoggersTests(unittest.TestCase):

    def setUp(self):
        self._saved = {name: logging.getLogger(name).level
                       for name in applog._NOISY_LOGGERS}
        self._env = os.environ.get(applog._DEBUG_ENV)
        os.environ.pop(applog._DEBUG_ENV, None)
        for name in applog._NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.NOTSET)

    def tearDown(self):
        for name, level in self._saved.items():
            logging.getLogger(name).setLevel(level)
        if self._env is None:
            os.environ.pop(applog._DEBUG_ENV, None)
        else:
            os.environ[applog._DEBUG_ENV] = self._env

    def test_matplotlib_is_pinned(self):
        applog.quiet_noisy_loggers()
        self.assertFalse(logging.getLogger("matplotlib").isEnabledFor(logging.DEBUG))

    def test_font_manager_child_is_covered_by_the_parent(self):
        """The flood came from matplotlib.font_manager, a child logger."""
        applog.quiet_noisy_loggers()
        self.assertFalse(
            logging.getLogger("matplotlib.font_manager").isEnabledFor(logging.DEBUG))

    def test_h5py_is_pinned(self):
        applog.quiet_noisy_loggers()
        self.assertFalse(logging.getLogger("h5py._conv").isEnabledFor(logging.DEBUG))

    def test_warnings_still_get_through(self):
        applog.quiet_noisy_loggers()
        self.assertTrue(
            logging.getLogger("matplotlib.font_manager").isEnabledFor(logging.WARNING))

    def test_samba_loggers_are_untouched(self):
        """Only the named libraries are pinned; our own trail stays at DEBUG."""
        root = logging.getLogger()
        saved = root.level
        try:
            root.setLevel(logging.DEBUG)
            applog.quiet_noisy_loggers()
            for name in ("core.hardware", "core.scan.runner", "config", "__main__"):
                self.assertTrue(logging.getLogger(name).isEnabledFor(logging.DEBUG),
                                f"{name} must keep full debug logging")
        finally:
            root.setLevel(saved)

    def test_debug_env_var_restores_everything(self):
        """SAMBA_LOG_DEBUG=1 is the escape hatch for debugging the libraries."""
        os.environ[applog._DEBUG_ENV] = "1"
        root = logging.getLogger()
        saved = root.level
        try:
            root.setLevel(logging.DEBUG)
            applog.quiet_noisy_loggers()
            self.assertTrue(
                logging.getLogger("matplotlib.font_manager").isEnabledFor(logging.DEBUG))
        finally:
            root.setLevel(saved)

    def test_setup_logging_applies_it(self):
        """It must be wired into setup_logging, not merely available."""
        import inspect
        src = inspect.getsource(applog.setup_logging)
        self.assertIn("quiet_noisy_loggers()", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
