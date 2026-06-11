"""One rule for where the ledger lives.

$FEYNMAN_HOME if set, else ~/.feynman-loop. No repo-relative magic: anchoring to the package
location breaks the moment the package is pip-installed (it would write into site-packages).
Existing installs that anchored to the repo set FEYNMAN_HOME explicitly in their configs.
"""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    p = Path(os.environ.get("FEYNMAN_HOME", str(Path.home() / ".feynman-loop")))
    p.mkdir(parents=True, exist_ok=True)
    return p
