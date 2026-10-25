"""Tests for the frozen-binary packaging helpers (ModuleNotFoundError fix)."""

from __future__ import annotations

from nebuko.engine.paths import LIB_DIR
from nebuko.engine.packaging import lib_datas


class TestLibDatas:
    def test_includes_the_config_package_sources(self):
        datas = dict(lib_datas())

        source = LIB_DIR / "config" / "sub_domains" / "workflow_step" / "output_field.py"
        assert source.is_file()
        assert str(source) in datas
        assert datas[str(source)].endswith(
            "nebuko/engine/lib/config/sub_domains/workflow_step"
        )

    def test_includes_python_files_even_though_collect_data_excludes_them(self):
        datas = lib_datas()

        assert any(src.endswith(".py") for src, _ in datas)
        assert any(src.endswith("event_normalizer.py") for src, _ in datas)

    def test_covers_every_file_under_lib(self):
        datas = lib_datas()

        expected = {
            (str(path), f"nebuko/engine/lib/{path.relative_to(LIB_DIR).parent}")
            for path in LIB_DIR.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        assert set(datas) == expected
