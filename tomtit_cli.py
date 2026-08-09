"""Repository-local bootstrap for the additive TOMTIT Product CLI V0.

Packaging integration is intentionally deferred. This new module makes the
CLI directly runnable from the repository as ``python -m tomtit_cli run``.
"""

from __future__ import annotations

import sys
from pathlib import Path


if __name__ == "__main__":
    source_root = Path(__file__).resolve().parent / "src"
    sys.path.insert(0, str(source_root))

    from tomtit_cli_v0 import main

    raise SystemExit(main())
