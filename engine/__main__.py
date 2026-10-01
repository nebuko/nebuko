"""``python -m engine`` — same entry point as ``python -m engine.run``."""

from engine.run_loop import main

if __name__ == "__main__":
    raise SystemExit(main())
