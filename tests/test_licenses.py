from __future__ import annotations

import io
import zipfile
from pathlib import Path

import repocapsule.licenses as licenses_mod
from repocapsule.interfaces import RepoContext
from repocapsule.licenses import (
    apply_license_to_context,
    detect_license_in_tree,
    detect_license_in_zip,
)


MIT_TEXT = """MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
"""

CC_BY_4_TEXT = """Creative Commons Attribution 4.0 International

To view a copy of this license, visit https://creativecommons.org/licenses/by/4.0/
or send a letter to Creative Commons, PO Box 1866, Mountain View, CA 94042, USA.
"""


def test_detect_license_in_tree_anchor(tmp_path: Path) -> None:
    (tmp_path / "LICENSE").write_text(MIT_TEXT, encoding="utf-8")
    detected, meta = detect_license_in_tree(tmp_path, None)
    assert detected == "MIT"
    assert meta["detect_method"] == "anchor_match"
    assert meta["license_path"] == "LICENSE"


def test_detect_license_in_tree_manifest(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"demo","license":"Apache-2.0"}',
        encoding="utf-8",
    )
    detected, meta = detect_license_in_tree(tmp_path, None)
    assert detected == "Apache-2.0"
    assert meta["detect_method"] == "manifest"
    assert meta["license_path"] == "package.json"


def test_detect_license_in_zip_subpath(tmp_path: Path) -> None:
    zip_path = tmp_path / "repo.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("repo-main/pkg/LICENSE", MIT_TEXT)
        zf.writestr("repo-main/pkg/README.md", "content")
    detected, meta = detect_license_in_zip(str(zip_path), "pkg")
    assert detected == "MIT"
    assert meta["license_path"] == "LICENSE"


def test_apply_license_to_context_merges_extra() -> None:
    ctx = RepoContext(repo_full_name="org/proj", extra={"source": "local"})
    updated = apply_license_to_context(
        ctx,
        "MIT",
        {
            "license_path": "LICENSE",
            "detect_method": "anchor_match",
            "license_sha256": "abc",
        },
    )
    assert updated.license_id == "MIT"
    assert updated.extra["source"] == "local"
    assert updated.extra["license_path"] == "LICENSE"


def test_detects_reuse_cc_license_when_no_other_license(tmp_path: Path) -> None:
    licenses_dir = tmp_path / "LICENSES"
    licenses_dir.mkdir()
    (licenses_dir / "CC-BY-4.0.txt").write_text(CC_BY_4_TEXT, encoding="utf-8")
    detected, meta = detect_license_in_tree(tmp_path, None)
    assert detected == "CC-BY-4.0"
    assert meta["detect_method"] == "reuse_license"
    assert meta["license_path"] == "LICENSES/CC-BY-4.0.txt"
    assert meta["content_scope"] == "repo"


def test_manifest_license_with_cc_content_metadata(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"demo","license":"MIT"}',
        encoding="utf-8",
    )
    licenses_dir = tmp_path / "LICENSES"
    licenses_dir.mkdir()
    (licenses_dir / "CC-BY-4.0.txt").write_text(CC_BY_4_TEXT, encoding="utf-8")
    detected, meta = detect_license_in_tree(tmp_path, None)
    assert detected == "MIT"
    assert meta["content_license_id"] == "CC-BY-4.0"
    assert meta["content_license_path"] == "LICENSES/CC-BY-4.0.txt"
    assert meta["content_license_detect_method"] == "reuse_license"
    assert meta["content_license_url"].startswith("https://creativecommons.org/licenses/by/4.0/")
    assert meta["content_scope"] == "repo"


def test_readme_cc_fallback(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "This work is licensed under a Creative Commons Attribution 4.0 International License. "
        "https://creativecommons.org/licenses/by/4.0/",
        encoding="utf-8",
    )
    detected, meta = detect_license_in_tree(tmp_path, None)
    assert detected == "CC-BY-4.0"
    assert meta["detect_method"] == "readme_cc"
    assert meta["license_path"] == "README.md"
    assert meta["content_scope"] == "repo"


def test_readme_cc_partial_scope_only_attaches_to_metadata(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"demo","license":"Apache-2.0"}',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "Images in this repo are licensed under Creative Commons Attribution 4.0 "
        "https://creativecommons.org/licenses/by/4.0/",
        encoding="utf-8",
    )
    detected, meta = detect_license_in_tree(tmp_path, None)
    assert detected == "Apache-2.0"
    assert meta["content_license_id"] == "CC-BY-4.0"
    assert meta["content_license_path"] == "README.md"
    assert meta["content_scope"] == "partial"


def test_readme_cc_partial_scope_not_reported_as_license(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "Screenshots under docs/images are licensed under Creative Commons BY 4.0 "
        "https://creativecommons.org/licenses/by/4.0/",
        encoding="utf-8",
    )
    detected, meta = detect_license_in_tree(tmp_path, None)
    assert detected is None
    assert meta == {}


def test_apply_license_to_context_preserves_content_metadata() -> None:
    ctx = RepoContext(repo_full_name="org/proj")
    updated = apply_license_to_context(
        ctx,
        "MIT",
        {
            "license_path": "LICENSE",
            "detect_method": "anchor_match",
            "license_sha256": "abc",
            "content_license_id": "CC-BY-4.0",
            "content_license_path": "LICENSES/CC-BY-4.0.txt",
            "content_license_detect_method": "reuse_license",
            "content_license_sha256": "def",
            "content_license_url": "https://creativecommons.org/licenses/by/4.0/",
            "content_attribution_hint": "Docs are CC-BY-4.0",
            "content_scope": "repo",
        },
    )
    assert updated.extra["content_license_id"] == "CC-BY-4.0"
    assert updated.extra["content_license_path"] == "LICENSES/CC-BY-4.0.txt"
    assert updated.extra["content_license_detect_method"] == "reuse_license"
    assert updated.extra["content_license_sha256"] == "def"
    assert updated.extra["content_license_url"].endswith("/by/4.0/")
    assert updated.extra["content_attribution_hint"] == "Docs are CC-BY-4.0"
    assert updated.extra["content_scope"] == "repo"


def test_spdx_expression_rejects_unknown_ids() -> None:
    assert licenses_mod._normalize_spdx_expression("MITT OR Apache-2.0") is None


def test_spdx_expression_accepts_known_ids() -> None:
    expr = licenses_mod._normalize_spdx_expression("MIT OR Apache-2.0")
    assert expr == "MIT OR Apache-2.0"
