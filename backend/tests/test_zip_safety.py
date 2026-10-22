"""
test_zip_safety.py
==================
WHAT THIS FILE TESTS
--------------------
The ZIP_* limits in extractor.py's archive handling. Each test builds a small
archive that breaks exactly one rule:

    too many members            -> the whole archive is rejected
    expands past the total cap  -> rejected (declared AND actually counted)
    compressed beyond the ratio -> rejected: the shape of a zip bomb
    "../" or absolute paths     -> that member is skipped, the rest extracted
    symlinks                    -> skipped
    zips inside zips            -> followed to ZIP_MAX_NESTING_DEPTH, then skipped

The limits are set low with monkeypatch so the archives stay tiny -- a real
zip bomb is not needed to prove the counting works.

TIER 1: pytest's tmp_path only.
"""

import io
import os
import zipfile

import pytest

from app.core.config import settings
from app.services.extractor import DocumentExtractor, UnsafeArchiveError


def make_zip(path, members: dict, compression=zipfile.ZIP_DEFLATED) -> str:
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return str(path)


def zip_bytes(members: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class TestLimits:
    def test_too_many_members(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "ZIP_MAX_ENTRIES", 3)
        archive = make_zip(tmp_path / "many.zip", {f"f{i}.txt": "x" for i in range(4)})
        with pytest.raises(UnsafeArchiveError, match="more than 3 files"):
            DocumentExtractor.extract(archive, "zip")

    def test_exactly_the_member_limit_is_fine(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "ZIP_MAX_ENTRIES", 3)
        archive = make_zip(tmp_path / "ok.zip", {f"f{i}.txt": f"file {i}" for i in range(3)})
        result = DocumentExtractor.extract(archive, "zip")
        assert result.metadata["file_count"] == 3

    def test_total_expansion_over_the_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES", 1000)
        archive = make_zip(tmp_path / "big.zip", {"a.txt": "a" * 600, "b.txt": "b" * 600})
        with pytest.raises(UnsafeArchiveError, match="expands to more than"):
            DocumentExtractor.extract(archive, "zip")

    def test_a_bomb_shaped_member_is_rejected(self, tmp_path, monkeypatch):
        """Two megabytes of zeros compress roughly 1000:1."""
        monkeypatch.setattr(settings, "ZIP_MAX_COMPRESSION_RATIO", 100)
        archive = make_zip(tmp_path / "bomb.zip", {"zeros.txt": b"\0" * (2 * 1024 * 1024)})
        with pytest.raises(UnsafeArchiveError, match="compressed"):
            DocumentExtractor.extract(archive, "zip")

    def test_small_repetitive_files_are_not_mistaken_for_bombs(self, tmp_path):
        """A tiny file can compress 200:1 and is harmless at any ratio."""
        archive = make_zip(tmp_path / "small.zip", {"spaces.txt": "Total " + " " * 50_000 + "end"})
        result = DocumentExtractor.extract(archive, "zip")
        assert result.metadata["file_count"] == 1

    def test_a_member_lying_about_its_size_never_yields_more_than_declared(self, tmp_path):
        """
        The header's size field is forged smaller than the real data, so the
        declared-size checks pass. The bytes actually produced must still be
        capped -- Python's zipfile stops at the declared size and then fails
        the CRC check, and our own counting is the second line of defence.
        Either way the member fails on its own; the archive is not trusted.
        """
        path = tmp_path / "liar.zip"
        make_zip(path, {"a.txt": "A" * 5000, "good.txt": "good text"}, compression=zipfile.ZIP_STORED)
        # Patch the uncompressed size, in both the local header and the
        # central directory, from 5000 down to 10.
        data = path.read_bytes()
        real, fake = (5000).to_bytes(4, "little"), (10).to_bytes(4, "little")
        assert data.count(real) >= 2
        path.write_bytes(data.replace(real, fake))

        result = DocumentExtractor.extract(str(path), "zip")

        assert "A" * 11 not in result.text
        assert "a.txt (extraction failed" in result.text
        assert "good text" in result.text


class TestUnsafeMembers:
    @pytest.mark.parametrize("name", ["../evil.txt", "a/../../evil.txt", "/etc/evil.txt", "C:/evil.txt"])
    def test_traversal_members_are_skipped_and_reported(self, tmp_path, name):
        archive = make_zip(tmp_path / "slip.zip", {name: "evil", "good.txt": "good text"})

        result = DocumentExtractor.extract(archive, "zip")

        assert result.metadata["files_extracted"] == ["good.txt"]
        assert any("unsafe path" in entry for entry in result.metadata["skipped_entries"])
        assert "evil" not in result.text.replace("good", "")
        # Nothing was written outside the archive's folder.
        assert not (tmp_path.parent / "evil.txt").exists()
        assert sorted(os.listdir(tmp_path)) == ["slip.zip"]

    def test_symlinks_are_skipped(self, tmp_path):
        path = tmp_path / "link.zip"
        with zipfile.ZipFile(path, "w") as archive:
            info = zipfile.ZipInfo("passwd.txt")
            info.external_attr = (0o120777 << 16)
            archive.writestr(info, "/etc/passwd")
            archive.writestr("good.txt", "good text")

        result = DocumentExtractor.extract(str(path), "zip")

        assert result.metadata["files_extracted"] == ["good.txt"]
        assert any("symbolic link" in entry for entry in result.metadata["skipped_entries"])

    def test_the_temp_folder_is_always_removed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES", 10)
        archive = make_zip(tmp_path / "big.zip", {"a.txt": "a" * 100})
        with pytest.raises(UnsafeArchiveError):
            DocumentExtractor.extract(archive, "zip")
        assert sorted(os.listdir(tmp_path)) == ["big.zip"]


class TestNesting:
    def test_one_level_of_nesting_is_followed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "ZIP_MAX_NESTING_DEPTH", 1)
        inner = zip_bytes({"inner.txt": "from the inner archive"})
        archive = make_zip(tmp_path / "outer.zip", {"inner.zip": inner, "outer.txt": "outer"})

        result = DocumentExtractor.extract(archive, "zip")

        assert "from the inner archive" in result.text

    def test_deeper_nesting_is_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "ZIP_MAX_NESTING_DEPTH", 1)
        innermost = zip_bytes({"deep.txt": "too deep to read"})
        middle = zip_bytes({"innermost.zip": innermost, "middle.txt": "middle text"})
        archive = make_zip(tmp_path / "outer.zip", {"middle.zip": middle})

        result = DocumentExtractor.extract(archive, "zip")

        assert "middle text" in result.text
        assert "too deep to read" not in result.text

    def test_depth_zero_skips_every_nested_archive(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "ZIP_MAX_NESTING_DEPTH", 0)
        inner = zip_bytes({"inner.txt": "inner text"})
        archive = make_zip(tmp_path / "outer.zip", {"inner.zip": inner})

        result = DocumentExtractor.extract(archive, "zip")

        assert "inner text" not in result.text
        assert any("too deep" in entry for entry in result.metadata["skipped_entries"])

    def test_nested_archives_share_one_budget(self, tmp_path, monkeypatch):
        """Ten inner zips must not get ten times the allowance."""
        monkeypatch.setattr(settings, "ZIP_MAX_ENTRIES", 6)
        inner = zip_bytes({f"f{i}.txt": "x" for i in range(3)})
        archive = make_zip(tmp_path / "outer.zip", {"a.zip": inner, "b.zip": inner})

        with pytest.raises(UnsafeArchiveError):
            DocumentExtractor.extract(archive, "zip")
