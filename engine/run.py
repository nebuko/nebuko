"""Stage-B frozen entry point: ``python -m engine.run``."""

from engine.run_loop import main

if __name__ == "__main__":
    raise SystemExit(main())
