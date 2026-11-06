"""Tests for the BS naming convention (renamer.py)."""

import os

import pytest

from app.renamer import build_name, part_suffix, rename_file, sanitize_title, validate


class TestSanitizeTitle:
    def test_basic(self):
        assert sanitize_title("Timepass Time") == "Timepass_Time"

    def test_strips_emoji(self):
        assert sanitize_title("Timepass Time \U0001F441\uFE0F") == "Timepass_Time"

    def test_strips_unicode_accents(self):
        assert sanitize_title("Café Résumé") == "Cafe_Resume"

    def test_collapses_underscores(self):
        assert sanitize_title("  a   b  ") == "a_b"
        assert sanitize_title("a__b___c") == "a_b_c"

    def test_removes_filesystem_illegal(self):
        assert sanitize_title('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"

    def test_empty_becomes_recording(self):
        assert sanitize_title("") == "Recording"
        assert sanitize_title("👁️👁️") == "Recording"

    def test_keeps_alnum(self):
        assert sanitize_title("Rank push to Immortal 2!") == "Rank_push_to_Immortal_2"


class TestPartSuffix:
    def test_padded(self):
        assert part_suffix(1) == "_P01"
        assert part_suffix(2) == "_P02"
        assert part_suffix(10) == "_P10"
        assert part_suffix(99) == "_P99"
        assert part_suffix(100) == "_P100"

    def test_none(self):
        assert part_suffix(None) == ""


class TestBuildName:
    def test_single(self):
        assert build_name("2025-10-08", "Rambling About Games") == "BS_2025-10-08_Rambling_About_Games"

    def test_multi(self):
        assert build_name("2025-10-08", "Rambling", 1) == "BS_2025-10-08_Rambling_P01"
        assert build_name("2025-10-08", "Rambling", 2) == "BS_2025-10-08_Rambling_P02"

    def test_bad_date_raises(self):
        with pytest.raises(ValueError):
            build_name("08-10-2025", "x")
        with pytest.raises(ValueError):
            build_name("", "x")

    def test_output_always_valid(self):
        for title in ["Timepass Time 👁️", "!!!", "a/b", ""]:
            name = build_name("2025-10-08", title)
            assert validate(name), name


class TestValidate:
    def test_valid(self):
        assert validate("BS_2025-10-08_Timepass_Time")
        assert validate("BS_2025-10-08_Timepass_Time_P01")

    def test_invalid(self):
        assert not validate("BS_2025-10-8_x")          # day not zero-padded
        assert not validate("bs_2025-10-08_x")          # lowercase
        assert not validate("BS_2025-10-08_")           # empty title
        assert not validate("BS_2025-10-08_a b")        # space
        assert not validate("BS_2025-10-08_x_y ")
        assert not validate("")
        assert not validate("VOD_2025-10-08_x")


class TestRenameFile:
    def test_rename(self, tmp_path):
        src = tmp_path / "rec_1.mp4"
        src.write_bytes(b"x" * 10)
        dst = rename_file(str(src), "BS_2025-10-08_Test")
        assert os.path.basename(dst) == "BS_2025-10-08_Test.mp4"
        assert os.path.exists(dst)
        assert not src.exists()

    def test_invalid_name_refused(self, tmp_path):
        src = tmp_path / "rec_1.mp4"
        src.write_bytes(b"x")
        with pytest.raises(ValueError):
            rename_file(str(src), "not_a_bs_name")

    def test_no_overwrite(self, tmp_path):
        src = tmp_path / "rec_1.mp4"
        src.write_bytes(b"x")
        existing = tmp_path / "BS_2025-10-08_Test.mp4"
        existing.write_bytes(b"y")
        with pytest.raises(FileExistsError):
            rename_file(str(src), "BS_2025-10-08_Test")
