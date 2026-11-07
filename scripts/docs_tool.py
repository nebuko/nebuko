#!/usr/bin/env python3
"""Docs tooling: link/ownership checks and website gathering.

``check``   deterministic rules (exit 1 on errors, warnings never fail):
              E1  a relative markdown link must resolve
              E2  a ``docs/...md`` path mentioned in source/tests must exist
              E3  a doc living outside ``docs/`` needs ``title`` + ``audience``
                  frontmatter
              E4  every ``covers:`` glob in frontmatter must match a file
              E5  a published page needs ``sidebar_group`` + ``sidebar_label``
                  (``sidebar_order`` optional) — the website builds its
                  sidebar from them
              W1  a ``*.py`` file named in backticks does not exist anywhere
              W2  ``verified_at`` is set and covered files changed since then
``gather``  copy ``docs/`` plus every colocated doc into ``<out>/docs`` so the
            website keeps one flat docs tree (``publish_as`` keeps old URLs).

Colocated docs are markdown files under ``src/`` or ``tests/``; they are
discovered by location, so a new feature doc needs no registration.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_URL = "https://github.com/nebuko/nebuko/blob/main/"
COLOCATED_ROOTS = ("src", "tests")
SKIP_PARTS = {".git", ".venv", "node_modules", "__pycache__", ".nebuko-engine", "demo"}
LINK = re.compile(r"(\]\()([^)#\s]+)(#[^)\s]*)?(\))")
DOC_MENTION = re.compile(r"docs/[A-Za-z0-9_./-]+\.md")
# Files whose ``docs/x.md`` strings are examples, not references.
EXEMPT_MENTIONS = {"docs_tool.py", "test_docs_tool.py", "test_workflow_patch.py"}
PY_NAME = re.compile(r"`([A-Za-z0-9_]+\.py)`")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter, body). Supports scalars and ``- item`` lists only."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    meta: dict = {}
    key = None
    for line in text[4:end].splitlines():
        if line.startswith("  - ") or line.startswith("- "):
            if key:
                meta.setdefault(key, []).append(line.split("- ", 1)[1].strip())
        elif ":" in line:
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip()
            meta[key] = val if val else []
    return meta, text[end + 4 :].lstrip("\n")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="surrogateescape")


def repo_files(root: Path) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS and d != "worktrees"]
        out += [Path(dirpath) / f for f in filenames]
    return out


def doc_files(root: Path) -> list[Path]:
    """Every markdown doc: all of ``docs/`` plus colocated ones under src/tests."""
    docs = [p for p in repo_files(root / "docs") if p.suffix == ".md"]
    colocated = [
        p
        for r in COLOCATED_ROOTS
        if (root / r).is_dir()
        for p in repo_files(root / r)
        if p.suffix == ".md" and not any("fixtures" in s or s.startswith(".") for s in p.parts)
    ]
    return sorted(docs + colocated)


def check(root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    files = repo_files(root)
    rel_files = {str(p.relative_to(root)) for p in files}
    basenames = {p.name for p in files}
    for doc in doc_files(root):
        rel = doc.relative_to(root)
        meta, body = parse_frontmatter(_read(doc))
        for m in LINK.finditer(body):
            tgt = m.group(2)
            if re.match(r"[a-z]+:", tgt) or tgt.startswith("/") or tgt == "…":
                continue
            if not (doc.parent / tgt).resolve().exists():
                errors.append(f"E1 {rel}: broken link -> {tgt}")
        if rel.parts[0] != "docs":
            for field in ("title", "audience"):
                if not meta.get(field):
                    errors.append(f"E3 {rel}: colocated doc missing frontmatter '{field}'")
        if _needs_sidebar(rel):
            for field in ("sidebar_group", "sidebar_label"):
                if not meta.get(field):
                    errors.append(f"E5 {rel}: missing frontmatter '{field}' (placement in the website sidebar)")
        covers = meta.get("covers") or []
        for pattern in covers if isinstance(covers, list) else [covers]:
            if not any(fnmatch.fnmatch(f, pattern) or fnmatch.fnmatch(f, pattern.replace("**", "*")) for f in rel_files):
                errors.append(f"E4 {rel}: covers '{pattern}' matches no file")
        for name in sorted(set(PY_NAME.findall(body))):
            if name not in basenames and name != "__init__.py":
                warnings.append(f"W1 {rel}: `{name}` not found in the repo")
        verified = meta.get("verified_at")
        if verified and covers:
            changed = _changed_since(root, verified)
            if changed is None:
                warnings.append(f"W2 {rel}: cannot resolve verified_at {verified}")
            else:
                hit = sorted(f for f in changed if any(fnmatch.fnmatch(f, c.replace("**", "*")) for c in covers))
                if hit:
                    warnings.append(f"W2 {rel}: {len(hit)} covered file(s) changed since {verified}, e.g. {hit[0]}")
    for p in files:
        if p.suffix not in (".py", ".yaml", ".yml"):
            continue
        rel = p.relative_to(root)
        if rel.parts[0] in ("docs", "demo", ".claude") or rel.name in EXEMPT_MENTIONS:
            continue
        for ref in sorted(set(DOC_MENTION.findall(_read(p)))):
            if ref not in rel_files and not _under_some_docs(ref, rel_files):
                errors.append(f"E2 {rel}: mentions {ref}, which does not exist")
    return errors, warnings


def _needs_sidebar(rel: Path) -> bool:
    """Published pages place themselves in the website sidebar. The Reading
    Guide (homepage) and ADRs (grouped by the website from their folder) don't."""
    if rel.parts[0] == "docs":
        return rel.name != "README.md" and "adr" not in rel.parts
    return True


def _under_some_docs(ref: str, rel_files: set[str]) -> bool:
    # ``docs/x.md`` used as an example string inside tests/fixtures is fine
    # only if some doc of that name exists; no leniency otherwise.
    return False


def _changed_since(root: Path, sha: str) -> set[str] | None:
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", f"{sha}..HEAD"],
            cwd=root, capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return set(out.split())


def published_name(root: Path, doc: Path, meta: dict) -> str:
    return meta.get("publish_as") or doc.name


def gather(root: Path, out: Path) -> list[str]:
    """Build ``<out>/docs`` from docs/ + colocated docs; returns problems."""
    docs_out = out / "docs"
    if docs_out.exists():
        shutil.rmtree(docs_out)
    problems: list[str] = []
    # original repo-relative path -> path inside the published docs tree
    published: dict[str, str] = {}
    for doc in doc_files(root):
        rel = doc.relative_to(root)
        meta, _ = parse_frontmatter(_read(doc))
        if rel.parts[0] == "docs":
            published[str(rel)] = str(rel.relative_to("docs"))
        else:
            name = published_name(root, doc, meta)
            if name in published.values():
                problems.append(f"name collision: {rel} -> {name}")
                continue
            published[str(rel)] = name
    for orig, dest in published.items():
        src = root / orig
        dst = docs_out / dest
        dst.parent.mkdir(parents=True, exist_ok=True)
        text = _read(src)
        meta, _ = parse_frontmatter(text)
        text = _rewrite_links(root, Path(orig), Path(dest), text, published)
        text = _for_site(text)
        dst.write_text(text, encoding="utf-8", errors="surrogateescape")
    for p in repo_files(root / "docs"):  # non-markdown assets (images, ...)
        if p.suffix != ".md":
            dst = docs_out / p.relative_to(root / "docs")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
    return problems


# Frontmatter keys that exist for the repo's own tooling, not for the site.
INTERNAL_KEYS = ("publish_as", "covers", "verified_at", "audience")


def _for_site(text: str) -> str:
    """Make a doc render cleanly on the site: drop repo-only frontmatter keys
    and, when ``title`` is set, the duplicate leading ``# Heading`` (the site
    renders the title as the page H1)."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    if end == -1:
        return text
    kept, skipping = [], False
    for line in text[4:end].splitlines():
        if line[:1] in (" ", "-") and skipping:
            continue
        key = line.split(":", 1)[0].strip()
        skipping = key in INTERNAL_KEYS
        if not skipping:
            kept.append(line)
    meta_text = "\n".join(kept)
    body = text[end + 4 :].lstrip("\n")
    if not re.search(r"^title:\s*\S", meta_text, re.M):
        # The site only derives a title from an H1 when the file has no
        # frontmatter at all, so do it here for files that have some.
        heading = re.match(r"#\s+([^\n]+)\n+", body)
        if heading:
            plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading.group(1))
            plain = re.sub(r"[*_`]", "", plain).strip()
            meta_text = f"title: {json.dumps(plain, ensure_ascii=False)}\n" + meta_text
    if re.search(r"^title:\s*\S", meta_text, re.M):
        body = re.sub(r"\A#\s+[^\n]*\n+", "", body, count=1)
    if not kept:
        return body
    return f"---\n{meta_text}\n---\n\n{body}"


def _rewrite_links(root: Path, orig: Path, dest: Path, text: str, published: dict[str, str]) -> str:
    def fix(m: re.Match) -> str:
        tgt = m.group(2)
        if re.match(r"[a-z]+:", tgt) or tgt.startswith("/") or tgt == "…":
            return m.group(0)
        target = os.path.normpath(os.path.join(os.path.dirname(orig), tgt))
        anchor = m.group(3) or ""
        if target in published:
            new = os.path.relpath(published[target], os.path.dirname(dest) or ".")
        elif orig.parts[0] == "docs" and (root / target).exists() is False:
            return m.group(0)
        elif orig.parts[0] != "docs" or target.split("/")[0] != "docs":
            # a code/config file: link to the repository instead of a dead path
            if not (root / target).exists():
                return m.group(0)
            new = REPO_URL + target
        else:
            return m.group(0)
        return m.group(1) + new + anchor + m.group(4)

    return LINK.sub(fix, text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="docs_tool")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--root", default=".")
    g = sub.add_parser("gather")
    g.add_argument("--root", default=".")
    g.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if args.cmd == "check":
        errors, warnings = check(root)
        for line in warnings:
            print("warning:", line)
        for line in errors:
            print("error:", line)
        print(f"{len(errors)} error(s), {len(warnings)} warning(s)")
        return 1 if errors else 0
    problems = gather(root, Path(args.out).resolve())
    for line in problems:
        print("error:", line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
