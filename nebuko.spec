# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the single-file release binary.

Built by the internal-release workflow. Mirrors the previous CLI flags
(``--onefile``) and adds data PyInstaller cannot discover on its own:
the ``engine/lib`` runtime tree, .py files included — ``collect_data_files``
excludes ``.py`` by default, which shipped binaries without the ``config``
module (ModuleNotFoundError at ``nebuko init``).
"""
from PyInstaller.utils.hooks import collect_data_files  # noqa: E402

from nebuko.engine.packaging import lib_datas  # noqa: E402

datas = lib_datas() + collect_data_files("litellm")

a = Analysis(
    ["packaging/entry.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="nebuko",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
)
