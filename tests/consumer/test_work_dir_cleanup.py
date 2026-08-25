"""Consumed downloads are removed; unconsumed ones are not.

The work directory held every download forever — the baseline archives (861 MB + 62 MB
against the current published release), the expanded Arango dump, and every diff bundle
ever applied. Nothing removed them, and the README's "transient" was aspirational.

Keeping them buys nothing: `HttpFetcher.download` streams to `<dest>.part` and renames
without ever checking whether `dest` exists, so a retry re-fetches regardless. A consumed
bundle is not a cache, it is a second copy of what is already in the stores.
"""

from pathlib import Path

import pytest

from consumer import baseline_import, fetcher, updater

# --- the primitive ---------------------------------------------------------


def test_discard_removes_a_file(tmp_path):
    f = tmp_path / "bundle.zst"
    f.write_text("x")
    fetcher.discard(f)
    assert not f.exists()


def test_discard_removes_a_directory(tmp_path):
    d = tmp_path / "dump"
    (d / "nested").mkdir(parents=True)
    (d / "nested" / "f").write_text("x")
    fetcher.discard(d)
    assert not d.exists()


def test_discard_is_silent_on_a_missing_path(tmp_path):
    fetcher.discard(tmp_path / "never-existed")  # must not raise


def test_discard_never_fails_an_otherwise_successful_update(tmp_path, monkeypatch):
    """A file we cannot remove is a disk-space problem, not a correctness one."""

    def boom(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "unlink", boom)
    fetcher.discard(tmp_path)  # must not raise


# --- the baseline scratch --------------------------------------------------


def _baseline_entry():
    return {
        "tag": "baseline-x",
        "head_sha": "sha-baseline",
        "assets": {"qdrant": "q.snapshot.zst", "arango": "a.tar.zst"},
        "sha256": {"qdrant": "qsha", "arango": "asha"},
    }


def _wire(tmp_path, *, restore_arango=None):
    """import_baseline with every side effect faked, files really created."""
    work = tmp_path / "work"

    def download_asset(tag, asset, dest, sha):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text("payload")
        return Path(dest)

    def decompress(path):
        d = work / "dump"
        d.mkdir(parents=True, exist_ok=True)
        (d / "data.json").write_text("{}")
        return d

    return dict(
        baseline_entry=_baseline_entry(),
        work_dir=work,
        download_asset=download_asset,
        decompress=decompress,
        restore_qdrant=lambda p: None,
        restore_arango=restore_arango or (lambda d: None),
        ensure_graph=lambda: None,
        ensure_lexical_index=lambda: "ready",
    )


def test_a_successful_restore_leaves_no_scratch_behind(tmp_path):
    kw = _wire(tmp_path)
    result = baseline_import.import_baseline(**kw)
    assert result["head_sha"] == "sha-baseline"
    leftovers = sorted(p.name for p in (tmp_path / "work").iterdir())
    assert leftovers == [], f"work dir still holds {leftovers}"


def test_a_failed_restore_keeps_its_scratch(tmp_path):
    """On a failure the archives stay put as evidence — and deleting during an error path
    is bad manners besides."""

    def explode(_dump):
        raise RuntimeError("arangorestore died")

    with pytest.raises(RuntimeError):
        baseline_import.import_baseline(**_wire(tmp_path, restore_arango=explode))
    assert sorted(p.name for p in (tmp_path / "work").iterdir())


# --- diff bundles ----------------------------------------------------------


class _Plan:
    mode = "diffs"
    baseline = None

    def __init__(self, diffs):
        self.diffs = diffs


def test_each_applied_diff_bundle_is_removed(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    diffs = [
        {"asset": "diff-a.jsonl.zst", "sha256": "s1", "head_sha": "h1"},
        {"asset": "diff-b.jsonl.zst", "sha256": "s2", "head_sha": "h2"},
    ]

    class FakeClient:
        def fetch_manifest(self):
            return {"schema_version": "2.0"}

        def download_asset(self, tag, asset, dest, sha):
            Path(dest).write_text("bundle")
            return Path(dest)

    monkeypatch.setattr(updater, "plan_update", lambda *a, **k: _Plan(diffs))
    monkeypatch.setattr(updater.bundle_mod, "read_bundle", lambda p: {"records": []})
    monkeypatch.setattr(updater, "apply_diff", lambda *a: None)

    result = updater.update(
        FakeClient(), object(), object(), tmp_path / ".cursor", work, ensure_index=None
    )
    assert result["applied"] == 2
    assert sorted(p.name for p in work.iterdir()) == [], "applied bundles must not linger"


def test_a_bundle_that_failed_to_apply_is_kept(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    diffs = [{"asset": "diff-a.jsonl.zst", "sha256": "s1", "head_sha": "h1"}]

    class FakeClient:
        def fetch_manifest(self):
            return {"schema_version": "2.0"}

        def download_asset(self, tag, asset, dest, sha):
            Path(dest).write_text("bundle")
            return Path(dest)

    def explode(*a):
        raise RuntimeError("bad bundle")

    monkeypatch.setattr(updater, "plan_update", lambda *a, **k: _Plan(diffs))
    monkeypatch.setattr(updater.bundle_mod, "read_bundle", lambda p: {"records": []})
    monkeypatch.setattr(updater, "apply_diff", explode)

    with pytest.raises(RuntimeError):
        updater.update(
            FakeClient(), object(), object(), tmp_path / ".cursor", work, ensure_index=None
        )
    assert (work / "diff-a.jsonl.zst").exists(), "a bundle that never applied is evidence"
