"""Allow the V0 facade to be run with ``python -m tomtit_cli_v0``."""

from .cli import main

raise SystemExit(main())
