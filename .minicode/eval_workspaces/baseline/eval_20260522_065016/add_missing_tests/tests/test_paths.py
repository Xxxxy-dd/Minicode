from pathlib import Path

from paths import filename, normalize_path


def test_normalize_path() -> None:
    assert normalize_path("a/b") == str(Path("a/b"))


def test_filename_for_posix_path() -> None:
    assert filename("a/b/file.txt") == "file.txt"
