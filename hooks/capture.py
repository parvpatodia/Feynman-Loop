#!/usr/bin/env python3
"""Shim kept for existing hook configs that point here. Canonical script lives in the package
(feynman_loop/assets/hooks/capture.py) so pip installs can ship it. Stdlib-only, fail-silent."""

import pathlib
import runpy
import sys

try:
    target = pathlib.Path(__file__).resolve().parent.parent / "feynman_loop/assets/hooks/capture.py"
    runpy.run_path(str(target), run_name="__main__")
except SystemExit:
    raise
except Exception:
    sys.exit(0)
