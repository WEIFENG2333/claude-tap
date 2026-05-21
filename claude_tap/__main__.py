"""Allow running as ``python -m claude_tap``."""

from claude_tap.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
