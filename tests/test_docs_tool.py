"""Docs tooling: the repo's docs must pass ``check`` and ``gather`` must keep
every colocated doc published under its old flat name."""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("docs_tool", REPO / "scripts" / "docs_tool.py")
docs_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(docs_tool)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_given_the_repo_when_checked_then_no_doc_errors():
    errors, _ = docs_tool.check(REPO)
    assert errors == []


def test_given_a_broken_link_when_checked_then_e1_names_the_doc(tmp_path):
    _write(tmp_path / "docs" / "a.md", "See [x](./missing.md).\n")
    errors, _ = docs_tool.check(tmp_path)
    assert any(e.startswith("E1 docs/a.md") for e in errors)


def test_given_a_colocated_doc_without_frontmatter_when_checked_then_e3(tmp_path):
    (tmp_path / "docs").mkdir()
    _write(tmp_path / "src" / "pkg" / "feature.md", "# Feature\n")
    errors, _ = docs_tool.check(tmp_path)
    assert sum(e.startswith("E3 src/pkg/feature.md") for e in errors) == 2


def test_given_a_covers_glob_matching_nothing_when_checked_then_e4(tmp_path):
    _write(tmp_path / "docs" / "a.md", "---\ncovers:\n  - src/gone/*.py\n---\n\n# A\n")
    errors, _ = docs_tool.check(tmp_path)
    assert any(e.startswith("E4 docs/a.md") for e in errors)


def test_given_code_mentioning_a_missing_doc_when_checked_then_e2(tmp_path):
    (tmp_path / "docs").mkdir()
    _write(tmp_path / "src" / "m.py", '"""See docs/gone.md."""\n')
    errors, _ = docs_tool.check(tmp_path)
    assert any(e.startswith("E2 src/m.py") for e in errors)


def test_given_colocated_docs_when_gathered_then_flat_names_and_links_resolve(tmp_path):
    root, out = tmp_path / "repo", tmp_path / "out"
    _write(root / "docs" / "index.md", "[feat](../src/pkg/feat.md) [code](../src/pkg/m.py)\n")
    _write(root / "src" / "pkg" / "m.py", "x = 1\n")
    _write(
        root / "src" / "pkg" / "feat.md",
        "---\ntitle: Feat\naudience: contributor\npublish_as: old-name.md\n---\n\n"
        "[back](../../docs/index.md) [code](m.py)\n",
    )
    assert docs_tool.gather(root, out) == []
    index = (out / "docs" / "index.md").read_text()
    feat = (out / "docs" / "old-name.md").read_text()
    assert "(./old-name.md)" in index or "(old-name.md)" in index
    assert "(index.md)" in feat
    assert "github.com/nebuko/nebuko/blob/main/src/pkg/m.py" in feat


def test_given_a_name_collision_when_gathered_then_it_is_reported(tmp_path):
    root = tmp_path / "repo"
    _write(root / "docs" / "same.md", "# A\n")
    _write(root / "src" / "p" / "same.md", "---\ntitle: S\naudience: contributor\n---\n")
    assert docs_tool.gather(root, tmp_path / "out") != []



def test_given_a_published_page_without_sidebar_keys_when_checked_then_e5(tmp_path):
    _write(tmp_path / "docs" / "a.md", "# A\n")
    _write(tmp_path / "docs" / "README.md", "# Home\n")
    _write(tmp_path / "docs" / "adr" / "0001-x.md", "# ADR\n")
    errors, _ = docs_tool.check(tmp_path)
    assert sum(e.startswith("E5 docs/a.md") for e in errors) == 2
    assert not any("README.md" in e or "adr/" in e for e in errors if e.startswith("E5"))


def test_given_frontmatter_without_title_when_gathered_then_h1_becomes_the_title(tmp_path):
    root, out = tmp_path / "repo", tmp_path / "out"
    _write(root / "docs" / "a.md", '---\nsidebar_group: "Guides"\n---\n\n# The `A` [page](x.md)\n\nBody.\n')
    assert docs_tool.gather(root, out) == []
    text = (out / "docs" / "a.md").read_text()
    assert text.startswith('---\ntitle: "The A page"\nsidebar_group: "Guides"\n---\n')
    assert "# The" not in text


def test_given_repo_only_keys_when_gathered_then_they_are_stripped_but_sidebar_keys_stay(tmp_path):
    root, out = tmp_path / "repo", tmp_path / "out"
    _write(root / "src" / "p" / "f.md",
           "---\ntitle: F\naudience: contributor\npublish_as: f.md\ncovers:\n  - src/p/*.py\n"
           'sidebar_group: "Guides"\nsidebar_label: "F"\n---\n\n# F\n\nBody.\n')
    assert docs_tool.gather(root, out) == []
    text = (out / "docs" / "f.md").read_text()
    for gone in ("audience", "publish_as", "covers", "src/p"):
        assert gone not in text
    assert 'sidebar_group: "Guides"' in text and "# F" not in text
