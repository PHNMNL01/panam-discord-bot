"""Independent DJ Panam application; enforce the supported Python floor on import."""

import sys


if sys.version_info < (3, 12):
    raise RuntimeError("DJ Panam requires Python 3.12 or newer for junction-safe path validation.")
