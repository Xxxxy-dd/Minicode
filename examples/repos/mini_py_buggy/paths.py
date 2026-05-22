from pathlib import Path


def normalize_path(path: str) -> str:
    return str(Path(path))


def filename(path: str) -> str:
    return path.split("/")[-1]
