import io
import os
import tempfile
import zipfile

import pytest

from app.core.config import settings
from app.services.extractor import DocumentExtractor, UnsafeArchiveError


@pytest.fixture
def scratch_temp(monkeypatch, tmp_path):
    """Point the SYSTEM temp directory at an empty folder of our own, so a test"""
    folder = tmp_path / "system-temp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


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
        """The header's size field is forged smaller than the real data, so the"""
        path = tmp_path / "liar.zip"
        make_zip(path, {"a.txt": "A" * 5000, "good.txt": "good text"}, compression=zipfile.ZIP_STORED)
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

    def test_the_temp_folder_is_always_removed(self, tmp_path, monkeypatch, scratch_temp):
        monkeypatch.setattr(settings, "ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES", 10)
        archive = make_zip(tmp_path / "big.zip", {"a.txt": "a" * 100})
        with pytest.raises(UnsafeArchiveError):
            DocumentExtractor.extract(archive, "zip")
        assert list(scratch_temp.iterdir()) == []

    def test_unpacking_happens_in_the_system_temp_dir_not_beside_the_archive(
        self, tmp_path, scratch_temp, monkeypatch
    ):
        """With local storage the archive sits in the uploads folder. A worker"""
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        inner = zip_bytes({"inner.txt": "inner text"})
        archive = make_zip(uploads / "a.zip", {"inner.zip": inner, "b.txt": "b text"})
        seen: list[str] = []
        real_copy = DocumentExtractor._copy_member

        def spy(archive_file, info, destination, budget):
            seen.append(os.path.dirname(destination))
            return real_copy(archive_file, info, destination, budget)

        monkeypatch.setattr(DocumentExtractor, "_copy_member", staticmethod(spy))

        result = DocumentExtractor.extract(archive, "zip")

        assert "inner text" in result.text and "b text" in result.text
        assert seen and all(path.startswith(str(scratch_temp)) for path in seen)
        assert sorted(os.listdir(uploads)) == ["a.zip"]
        assert list(scratch_temp.iterdir()) == []


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


def office_file(path, extension: str, body: str = "<w:document>hello</w:document>") -> str:
    """A minimal zip shaped like an Office file, with one main XML part."""
    part = {"docx": "word/document.xml", "pptx": "ppt/presentation.xml", "xlsx": "xl/workbook.xml"}
    return make_zip(path, {"[Content_Types].xml": "<Types/>", part[extension]: body})


class TestOfficeContainers:
    @pytest.mark.parametrize("extension", ["docx", "pptx", "xlsx"])
    def test_a_document_that_expands_past_the_cap_is_rejected(self, tmp_path, monkeypatch, extension):
        """A few KB on disk, megabytes once its parser opens it."""
        monkeypatch.setattr(settings, "ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES", 1024 * 1024)
        path = office_file(tmp_path / f"bomb.{extension}", extension, body="<p/>" * 400_000)
        assert os.path.getsize(path) < 50_000

        with pytest.raises(UnsafeArchiveError, match="expands to more than"):
            DocumentExtractor.extract(path, extension)

    def test_the_parser_never_sees_a_rejected_document(self, tmp_path, monkeypatch):
        """The check has to come first: the parser decompresses into memory."""
        monkeypatch.setattr(settings, "ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES", 1024)
        path = office_file(tmp_path / "bomb.docx", "docx", body="x" * 5000)
        opened = []
        monkeypatch.setattr(DocumentExtractor, "extract_docx", classmethod(lambda cls, p: opened.append(p)))

        with pytest.raises(UnsafeArchiveError):
            DocumentExtractor.extract(path, "docx")
        assert opened == []

    def test_a_real_document_is_not_judged_on_count_or_ratio(self, tmp_path, monkeypatch):
        """Legitimate Office files break both archive limits: a 400-slide deck"""
        import docx

        monkeypatch.setattr(settings, "ZIP_MAX_ENTRIES", 1)
        monkeypatch.setattr(settings, "ZIP_MAX_COMPRESSION_RATIO", 2)
        document = docx.Document()
        for _ in range(200):
            document.add_paragraph("The same boilerplate sentence, over and over again.")
        path = tmp_path / "real.docx"
        document.save(path)

        result = DocumentExtractor.extract(str(path), "docx")

        assert "boilerplate sentence" in result.text

    def test_a_file_that_is_not_a_zip_is_left_to_its_parser(self, tmp_path):
        """No UnsafeArchiveError: the parser reports what is wrong in its own words."""
        path = tmp_path / "broken.docx"
        path.write_bytes(b"this was never a zip archive")

        result = DocumentExtractor.extract(str(path), "docx")

        assert "DOCX extraction failed" in result.text

    def test_a_bomb_inside_an_uploaded_zip_fails_the_whole_archive(self, tmp_path, monkeypatch):
        """The docx is SMALL inside the outer zip, so the outer archive's own"""
        monkeypatch.setattr(settings, "ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES", 1024 * 1024)
        bomb = office_file(tmp_path / "inner.docx", "docx", body="<p/>" * 400_000)
        with open(bomb, "rb") as handle:
            archive = make_zip(tmp_path / "outer.zip", {"report.docx": handle.read(), "a.txt": "fine"})

        with pytest.raises(UnsafeArchiveError):
            DocumentExtractor.extract(archive, "zip")
