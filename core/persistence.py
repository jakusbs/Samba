"""Atomic JSON persistence shared by setup files and the device registry."""
import json
import os
from pathlib import Path
import tempfile


def atomic_write_json(path, value):
    """Replace *path* only after serialization and a durable write succeed.

    A unique sibling avoids collisions between saves. Errors propagate so the
    caller can report failure instead of claiming the configuration was saved.
    """
    path = Path(path)
    payload = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                     dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
