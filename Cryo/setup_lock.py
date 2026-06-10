"""Re-export of the shared setup-lock client (see core/setup_lock.py)."""
from core.setup_lock import (acquire_lock, release_lock, check_lock,  # noqa: F401
                             LOCK_DEVICE, STALE_LOCK_HOURS, TANGO_AVAILABLE)
