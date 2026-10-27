#!/usr/bin/env python3
"""Materialize the deployable output for each website (site, docs, blog).

Reads per-site maintenance flags from ``maintenance.yaml`` at the repo root
and writes each site's output into a directory named after the site.

- Flag on  → the maintenance page + logo are copied into the output directory,
  replacing any previous ``index.html``/``logo.svg`` (existing other content is
  left alone so flipping the flag back restores it).
- Flag off → existing content is kept untouched; if the site has no
  ``index.html`` yet, a minimal placeholder is written.

Usage: ``uv run python scripts/build_sites.py``
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

SITES: tuple[str, ...] = ("site", "docs", "blog")

_MAINTENANCE_DIR = Path(__file__).resolve().parent.parent / "maintenance"

_PLACEHOLDER = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nebuko — coming soon</title>
<style>
  body { min-height: 100svh; display: flex; align-items: center; justify-content: center;
         background: #0b0a1a; color: #e8e6f5; text-align: center;
         font-family: system-ui, sans-serif; }
</style>
</head>
<body><main><h1>Coming soon</h1></main></body>
</html>
"""


class SiteBuilder:
    """Builds per-site deploy outputs driven by the maintenance flags."""

    def __init__(self, config_path: Path, output_root: Path) -> None:
        self.config_path = config_path
        self.output_root = output_root

    def load_config(self) -> dict[str, bool]:
        """Parses maintenance.yaml; missing/unknown keys default to True."""
        flags = dict.fromkeys(SITES, True)
        if self.config_path.exists():
            data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
            for site, value in (data.get("maintenance") or {}).items():
                flags[str(site)] = bool(value)
        return flags

    def build(self) -> list[Path]:
        """Enabled → copy maintenance page + logo; disabled → keep existing content."""
        outputs: list[Path] = []
        for site, enabled in self.load_config().items():
            if enabled:
                outputs.append(self._copy_maintenance(site))
            elif not (self.output_root / site / "index.html").exists():
                outputs.append(self._write_placeholder(site))
        return outputs

    def _copy_maintenance(self, site: str) -> Path:
        out_dir = self.output_root / site
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(_MAINTENANCE_DIR / "index.html", out_dir / "index.html")
        shutil.copy(_MAINTENANCE_DIR / "logo.svg", out_dir / "logo.svg")
        return out_dir / "index.html"

    def _write_placeholder(self, site: str) -> Path:
        out_dir = self.output_root / site
        out_dir.mkdir(parents=True, exist_ok=True)
        placeholder = out_dir / "index.html"
        placeholder.write_text(_PLACEHOLDER, encoding="utf-8")
        return placeholder


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    builder = SiteBuilder(repo_root / "maintenance.yaml", repo_root)
    for path in builder.build():
        print(f"built {path.relative_to(repo_root)}")


if __name__ == "__main__":
    main()
