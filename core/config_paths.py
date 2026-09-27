"""Config location resolved after the selected launcher sets its environment."""
import os
from pathlib import Path
CONFIG_DIR = Path(os.environ.get("SAMBA_CONFIG_DIR") or Path.home() / ".config" / "moke_scan").expanduser()
