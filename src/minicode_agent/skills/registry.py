from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SkillError(Exception):
    """Expected skill loading error shown to users and tests."""


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    applies_to: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SkillDefinition:
    metadata: SkillMetadata
    content: str
    path: Path

    @property
    def name(self) -> str:
        return self.metadata.name


class SkillRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or builtin_skills_path()
        self._skills: dict[str, SkillDefinition] | None = None

    def list(self) -> list[SkillDefinition]:
        return [self._loaded()[name] for name in sorted(self._loaded())]

    def get(self, name: str) -> SkillDefinition:
        try:
            return self._loaded()[name]
        except KeyError as exc:
            raise SkillError(f"Unknown skill: {name}") from exc

    def _loaded(self) -> dict[str, SkillDefinition]:
        if self._skills is None:
            self._skills = load_skills(self.root)
        return self._skills


def builtin_skills_path() -> Path:
    return Path(__file__).parent / "builtin"


def load_skills(root: Path) -> dict[str, SkillDefinition]:
    if not root.exists():
        raise SkillError(f"Skill root does not exist: {root}")
    skills: dict[str, SkillDefinition] = {}
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        skill = load_skill(directory)
        if skill.name in skills:
            raise SkillError(f"Duplicate skill name: {skill.name}")
        skills[skill.name] = skill
    return skills


def load_skill(directory: Path) -> SkillDefinition:
    metadata_path = directory / "metadata.yaml"
    content_path = directory / "SKILL.md"
    if not metadata_path.exists():
        raise SkillError(f"Missing metadata.yaml for skill: {directory.name}")
    if not content_path.exists():
        raise SkillError(f"Missing SKILL.md for skill: {directory.name}")

    metadata = parse_skill_metadata(metadata_path.read_text(encoding="utf-8"), metadata_path)
    content = content_path.read_text(encoding="utf-8").strip()
    if not content:
        raise SkillError(f"Skill content is empty: {content_path}")
    return SkillDefinition(metadata=metadata, content=content, path=directory)


def parse_skill_metadata(text: str, source: Path | None = None) -> SkillMetadata:
    payload = parse_simple_yaml(text)
    source_name = str(source or "metadata.yaml")
    required = {
        "name": str,
        "description": str,
        "tags": list,
        "applies_to": list,
        "examples": list,
    }
    for key, expected_type in required.items():
        if key not in payload:
            raise SkillError(f"Missing required metadata field '{key}' in {source_name}")
        if not isinstance(payload[key], expected_type):
            raise SkillError(f"Metadata field '{key}' has invalid type in {source_name}")
    if not payload["name"].strip():
        raise SkillError(f"Metadata field 'name' must be non-empty in {source_name}")
    if not payload["description"].strip():
        raise SkillError(f"Metadata field 'description' must be non-empty in {source_name}")
    return SkillMetadata(
        name=payload["name"],
        description=payload["description"],
        tags=payload["tags"],
        applies_to=payload["applies_to"],
        examples=payload["examples"],
        aliases=payload.get("aliases", []),
    )


def parse_simple_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if current_key is None or not isinstance(result.get(current_key), list):
                raise SkillError(f"List item without list field: {stripped}")
            result[current_key].append(parse_scalar(stripped[2:]))
            continue
        if ":" not in stripped:
            raise SkillError(f"Invalid metadata line: {stripped}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise SkillError(f"Invalid metadata key: {stripped}")
        if value:
            result[key] = parse_scalar(value)
            current_key = None
        else:
            result[key] = []
            current_key = key
    return result


def parse_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
