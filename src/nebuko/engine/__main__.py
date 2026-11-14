"""``python -m nebuko.engine`` — same entry point as ``python -m nebuko.engine.run``."""

from nebuko.engine.run_loop import main

if __name__ == "__main__":
    raise SystemExit(main())
