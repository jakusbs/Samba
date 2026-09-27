from core.scan.runner import ScanRunner

__all__ = ["ScanRunner", "ScanWorker", "ScanlistWorker"]


def __getattr__(name):
    # Pure acquisition code can be imported without a Qt installation.
    if name in ("ScanWorker", "ScanlistWorker"):
        from core.scan import workers
        return getattr(workers, name)
    raise AttributeError(name)
