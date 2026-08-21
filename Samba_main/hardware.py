from core.hardware import *  # noqa: F401,F403
# `import *` skips underscore names, so private ones a caller may need have to
# be re-exported explicitly — the omission is what produced
# "cannot import name '_pcache' from 'hardware'".  Kept identical to
# Cryo/hardware.py so the two shims cannot drift.
from core.hardware import _pcache, _pcache_lock  # noqa: F401
