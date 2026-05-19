#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Tests for image accessors, the HTML manifest, and downstream invalidation."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from paperstore import (
    ClearedSet,
    HtmlImageEntry,
    HtmlImagesManifest,
    HtmlManifestError,
    SqliteBackend,
)


@pytest.fixture
def store(tmp_path: Path) -> SqliteBackend:
    return SqliteBackend(tmp_path)


# ---- image artifact accessors ---------------------------------------------


def test_write_and_get_paper_image_roundtrip(store: SqliteBackend, tmp_path: Path):
    data = b"\x89PNG\r\n\x1a\n_synthetic_"
    path = store.write_paper_image("P3556R0", page=3, index=1, ext="png", data=data)
    assert path == tmp_path / "paperstore" / "p3556r0-fig3-1.png"
    assert path.read_bytes() == data
    assert store.get_paper_image_path("P3556R0", 3, 1, "png") == path


def test_get_paper_image_path_lowercases_pid_and_ext(store: SqliteBackend):
    path = store.get_paper_image_path("P3556R0", 3, 1, "PNG")
    assert path.name == "p3556r0-fig3-1.png"


def test_get_paper_image_path_strips_leading_dot_in_ext(store: SqliteBackend):
    path = store.get_paper_image_path("P3556R0", 3, 1, ".png")
    assert path.name == "p3556r0-fig3-1.png"


def test_get_paper_image_path_does_not_check_existence(store: SqliteBackend):
    path = store.get_paper_image_path("NEVER_WRITTEN", 1, 1, "png")
    assert not path.exists()


def test_get_paper_image_path_html_uses_page_zero(store: SqliteBackend):
    """page=0 is the HTML 'no page concept' sentinel."""
    path = store.get_paper_image_path("P3556R0", 0, 2, "jpeg")
    assert path.name == "p3556r0-fig0-2.jpeg"


def test_write_paper_image_is_atomic(store: SqliteBackend, tmp_path: Path):
    store.write_paper_image("P1", 1, 1, "png", b"x")
    leftovers = list((tmp_path / "paperstore").glob("*.partial"))
    assert leftovers == []


def test_write_paper_image_overwrites(store: SqliteBackend):
    p1 = store.write_paper_image("P1", 1, 1, "png", b"v1")
    p2 = store.write_paper_image("P1", 1, 1, "png", b"v2")
    assert p1 == p2
    assert p1.read_bytes() == b"v2"


# ---- iter / delete ----------------------------------------------------------


def test_iter_paper_image_paths_empty(store: SqliteBackend):
    assert list(store.iter_paper_image_paths("NOPE")) == []


def test_iter_paper_image_paths_yields_only_target_paper(store: SqliteBackend):
    store.write_paper_image("P1234R0", 1, 1, "png", b"a")
    store.write_paper_image("P1234R0", 2, 1, "png", b"b")
    store.write_paper_image("P9999R0", 1, 1, "png", b"other")

    names = [p.name for p in store.iter_paper_image_paths("P1234R0")]
    assert names == ["p1234r0-fig1-1.png", "p1234r0-fig2-1.png"]


def test_iter_paper_image_paths_deterministic_order(store: SqliteBackend):
    # Insert out-of-order; iteration must be alphanumeric on filename,
    # which equals (page, index) ascending by construction.
    store.write_paper_image("P1", 2, 1, "png", b"a")
    store.write_paper_image("P1", 1, 2, "png", b"b")
    store.write_paper_image("P1", 1, 1, "png", b"c")
    store.write_paper_image("P1", 10, 1, "png", b"d")

    names = [p.name for p in store.iter_paper_image_paths("P1")]
    # Note: lexicographic ordering, so "p1-fig10-1" sorts before "p1-fig2-1".
    # That's fine - the contract is "deterministic", not "numeric".
    assert names == sorted(names)


def test_iter_paper_image_paths_ignores_non_matching_filenames(
    store: SqliteBackend, tmp_path: Path
):
    papers_dir = tmp_path / "paperstore"
    # Real image
    store.write_paper_image("P1234R0", 1, 1, "png", b"real")
    # Decoys that share the pid prefix but are not image artifacts
    (papers_dir / "p1234r0.md").write_text("body")
    (papers_dir / "p1234r0.prompts.json").write_text("[]")
    (papers_dir / "p1234r0-meta.json").write_text("{}")
    # Malformed filenames that the regex must reject
    (papers_dir / "p1234r0-fig1.png").write_bytes(b"missing-index")
    (papers_dir / "p1234r0-fig1-1-extra.png").write_bytes(b"trailing-suffix")
    (papers_dir / "p1234r0-fig1-1.png.partial").write_bytes(b"in-flight")

    names = [p.name for p in store.iter_paper_image_paths("P1234R0")]
    assert names == ["p1234r0-fig1-1.png"]


def test_delete_paper_images_returns_count(store: SqliteBackend):
    store.write_paper_image("P1", 1, 1, "png", b"a")
    store.write_paper_image("P1", 1, 2, "png", b"b")
    store.write_paper_image("P1", 2, 1, "png", b"c")
    assert store.delete_paper_images("P1") == 3


def test_delete_paper_images_only_targets_own_paper(store: SqliteBackend):
    store.write_paper_image("P1", 1, 1, "png", b"keep-me")
    store.write_paper_image("P2", 1, 1, "png", b"delete-me")
    assert store.delete_paper_images("P2") == 1
    assert (store.workspace_dir / "paperstore" / "p1-fig1-1.png").exists()
    assert not (store.workspace_dir / "paperstore" / "p2-fig1-1.png").exists()


def test_delete_paper_images_returns_zero_when_none(store: SqliteBackend):
    assert store.delete_paper_images("NOTHING") == 0


def test_delete_paper_images_glob_safety_regression(
    store: SqliteBackend, tmp_path: Path
):
    """`delete_paper_images('P30')` must NOT touch `p301-...` or `p30-meta.json`.

    This guards the pid-prefix collision shape called out in plan §2.2 and
    the named-regex contract in `_IMAGE_FILENAME_RE`.
    """
    papers_dir = tmp_path / "paperstore"
    p30 = store.write_paper_image("P30", 1, 1, "png", b"belongs-to-p30")
    p301 = store.write_paper_image("P301", 1, 1, "png", b"belongs-to-p301")
    decoy = papers_dir / "p30-meta.json"
    decoy.write_text("{}")

    removed = store.delete_paper_images("P30")
    assert removed == 1
    assert not p30.exists()
    assert p301.exists() and p301.read_bytes() == b"belongs-to-p301"
    assert decoy.exists()


def test_delete_paper_images_case_insensitive_pid(store: SqliteBackend):
    """Caller can use any casing; on-disk form is always lowercase."""
    p = store.write_paper_image("P3556R0", 3, 1, "png", b"x")
    assert store.delete_paper_images("p3556r0") == 1
    assert not p.exists()


# ---- HTML manifest path -----------------------------------------------------


def test_get_html_images_manifest_path(store: SqliteBackend, tmp_path: Path):
    path = store.get_html_images_manifest_path("P3556R0")
    assert path == tmp_path / "paperstore" / "p3556r0.html-images.json"
    assert not path.exists()  # path-only accessor


def test_get_html_images_manifest_path_roundtrip(store: SqliteBackend):
    path = store.get_html_images_manifest_path("P1")
    manifest = HtmlImagesManifest(
        pid="P1",
        entries=[
            HtmlImageEntry(
                original_src="https://example.com/fig.png",
                stored_filename="p1-fig0-1.png",
                document_order=1,
                caption_text="Figure 1: example",
                alt_attr="example image",
            ),
        ],
    )
    path.write_text(manifest.to_json(), encoding="utf-8")
    loaded = HtmlImagesManifest.from_json(path.read_text(encoding="utf-8"))
    assert loaded == manifest


# ---- HtmlImagesManifest forward-compat (N5) ---------------------------------


def test_manifest_v1_roundtrip():
    manifest = HtmlImagesManifest(
        pid="P3556R0",
        entries=[
            HtmlImageEntry(
                original_src="a.png", stored_filename="p3556r0-fig0-1.png",
                document_order=1, caption_text="cap", alt_attr="alt",
            ),
        ],
    )
    loaded = HtmlImagesManifest.from_json(manifest.to_json())
    assert loaded == manifest
    assert loaded.version == 1


def test_manifest_accepts_v2_with_unknown_fields():
    """Forward compat: a v=2 envelope with extra fields is accepted and the
    v1 reader parses the known fields."""
    raw = json.dumps({
        "version": 2,
        "pid": "P3556R0",
        "license": "BSL-1.0",  # unknown top-level field
        "entries": [
            {
                "original_src": "a.png",
                "stored_filename": "p3556r0-fig0-1.png",
                "document_order": 1,
                "caption_text": "cap",
                "alt_attr": "alt",
                "checksum_sha256": "deadbeef",  # unknown per-entry field
            },
        ],
    })
    loaded = HtmlImagesManifest.from_json(raw)
    assert loaded.version == 2
    assert loaded.pid == "P3556R0"
    assert len(loaded.entries) == 1
    assert loaded.entries[0].original_src == "a.png"
    assert loaded.entries[0].caption_text == "cap"


def test_manifest_rejects_too_new_version():
    raw = json.dumps({
        "version": 99,
        "pid": "P3556R0",
        "entries": [],
    })
    with pytest.raises(HtmlManifestError, match="newer than"):
        HtmlImagesManifest.from_json(raw)


def test_manifest_rejects_malformed_envelope():
    with pytest.raises(HtmlManifestError, match="valid JSON"):
        HtmlImagesManifest.from_json("not-json")
    with pytest.raises(HtmlManifestError, match="JSON object"):
        HtmlImagesManifest.from_json("[]")
    with pytest.raises(HtmlManifestError, match="must be an integer"):
        HtmlImagesManifest.from_json(json.dumps({"version": "one"}))


# ---- clear_downstream_outputs -----------------------------------------------


def _make_loc(line=1, start_char=0, end_char=10):
    return SimpleNamespace(line=line, start_char=start_char, end_char=end_char)


def _make_claim(text="claim", section="intro", question="why?", line=1, uid=1):
    return SimpleNamespace(
        uid=uid,
        loc=_make_loc(line=line),
        text=text,
        section=section,
        question=question,
        merged_into=None,
    )


def test_cleared_set_truthiness_and_names():
    assert not ClearedSet()
    assert bool(ClearedSet(dissect=True))
    assert ClearedSet(dissect=True, agora=True).names() == ["dissect", "agora"]
    assert ClearedSet(advocatus=True, agora=True).names() == ["advocatus", "agora"]


def test_clear_downstream_outputs_unknown_paper(store: SqliteBackend):
    cleared = store.clear_downstream_outputs("DOES-NOT-EXIST")
    assert cleared == ClearedSet()
    assert not cleared


def test_clear_downstream_outputs_no_artifacts(store: SqliteBackend):
    """Paper exists in the index but has no downstream artifacts."""
    store.upsert_year("2026", [{"paper_id": "P1"}])
    cleared = store.clear_downstream_outputs("P1")
    assert cleared == ClearedSet()


def test_clear_downstream_outputs_full_sweep(store: SqliteBackend):
    """Seed all three pipelines (files + DB rows); ensure the sweep is complete.

    Also asserts: paper.md is preserved, image files are preserved.
    """
    pid = "P1000R0"
    store.upsert_year("2026", [{"paper_id": pid}])
    md_path = store.write_paper_md(pid, "# Body\n")
    image_path = store.write_paper_image(pid, 1, 1, "png", b"image-bytes")

    dissect_path = store.write_dissect_md(pid, "# Dissect\n")
    advocatus_path = store.write_advocatus_md(pid, "# Relatio\n")
    agora_path = store.write_agora_json(pid, {"threads": []})

    store.store_claims(pid, [_make_claim(text="A", uid=1)])
    store.store_rhetoric(pid, [
        SimpleNamespace(
            uid=1, loc=_make_loc(line=5), text="however", section="intro",
            marker_type="hedge", target="claim", intensity="low",
        ),
    ])
    store.store_paper_citations(pid, [
        SimpleNamespace(paper_id="P9999R0", count=2),
    ])
    store.store_caput_causae(pid, "the thesis")

    # Pre-sweep sanity
    assert dissect_path.exists()
    assert advocatus_path.exists()
    assert agora_path.exists()
    assert len(store.get_claims(pid)) == 1
    assert len(store.get_rhetoric(pid)) == 1
    assert len(store.get_paper_citations(pid)) == 1
    assert store.get_caput_causae(pid) is not None

    cleared = store.clear_downstream_outputs(pid)

    assert cleared == ClearedSet(dissect=True, advocatus=True, agora=True)
    assert cleared.names() == ["dissect", "advocatus", "agora"]

    # Files gone
    assert not dissect_path.exists()
    assert not advocatus_path.exists()
    assert not agora_path.exists()

    # Extract rows gone
    assert store.get_claims(pid) == []
    assert store.get_rhetoric(pid) == []
    assert store.get_paper_citations(pid) == []
    assert store.get_caput_causae(pid) is None

    # Meta paths cleared
    meta = store.get_meta(pid)
    assert meta.dissect_path == ""
    assert meta.advocatus_path == ""
    assert meta.agora_path == ""

    # paper.md AND image bytes untouched
    assert md_path.exists() and md_path.read_text(encoding="utf-8") == "# Body\n"
    assert meta.markdown_path == str(md_path)
    assert image_path.exists() and image_path.read_bytes() == b"image-bytes"


def test_clear_downstream_outputs_partial(store: SqliteBackend):
    """Only the pipelines that had data are reported as cleared."""
    pid = "P1000R0"
    store.upsert_year("2026", [{"paper_id": pid}])
    store.write_dissect_md(pid, "# Dissect")
    # advocatus and agora never ran

    cleared = store.clear_downstream_outputs(pid)
    assert cleared == ClearedSet(dissect=True)
    assert cleared.names() == ["dissect"]
