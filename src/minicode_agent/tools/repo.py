import json
from pathlib import Path
from typing import Any

from minicode_agent.tools.base import BaseTool
from minicode_agent.tools.readonly import DEFAULT_EXCLUDES, should_exclude
from minicode_agent.tools.types import DuplicatePolicy, PermissionMode, RiskLevel, ToolContext, ToolIntent, ToolSpec, ToolStateEffect

INSPECT_REPO_EXCLUDES = {*DEFAULT_EXCLUDES, ".minicode", ".pytest*"}


LANGUAGE_BY_SUFFIX = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".md": "Markdown",
}

ENTRY_FILE_NAMES = (
    "README.md",
    "README.rst",
    "README.txt",
    "pyproject.toml",
    "package.json",
    "setup.py",
    "Cargo.toml",
    "go.mod",
)

TEST_COMMAND_CANDIDATES = {
    "pyproject.toml": "python -m pytest",
    "pytest.ini": "python -m pytest",
    "package.json": "npm test",
    "Cargo.toml": "cargo test",
    "go.mod": "go test ./...",
}


class InspectRepoTool(BaseTool):
    spec = ToolSpec(
        name="inspect_repo",
        description="Return a structured summary of workspace files, languages, entry files, and test command candidates.",
        input_schema={
            "type": "object",
            "properties": {
                "max_files": {
                    "type": "integer",
                    "description": "Maximum files to sample while inspecting the repository.",
                    "default": 200,
                    "minimum": 1,
                },
            },
        },
        risk_level=RiskLevel.SAFE,
        permission=PermissionMode.ALLOW,
        duplicate_policy=DuplicatePolicy.BLOCK_IDENTICAL_SUCCESS,
        state_effects=(ToolStateEffect.RECORDS_PATH_FACT, ToolStateEffect.RECORDS_OUTPUT_FACT),
        intents=(ToolIntent.REPO_INSPECT,),
        capability="repo_inspection",
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        max_files = int(arguments.get("max_files", 200))
        root = context.resolved_workspace
        files: list[str] = []
        languages: dict[str, int] = {}
        truncated = False

        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if not path.is_file() or should_exclude(rel, INSPECT_REPO_EXCLUDES):
                continue
            rel_text = str(rel).replace("\\", "/")
            files.append(rel_text)
            language = LANGUAGE_BY_SUFFIX.get(path.suffix.lower())
            if language:
                languages[language] = languages.get(language, 0) + 1
            if len(files) >= max_files:
                truncated = True
                break

        entry_files = choose_entry_files(files)
        test_commands = choose_test_commands(files)
        summary = {
            "file_count_sampled": len(files),
            "truncated": truncated,
            "top_level": choose_top_level(files),
            "languages": dict(sorted(languages.items(), key=lambda item: (-item[1], item[0]))),
            "entry_files": entry_files,
            "test_commands": test_commands,
        }
        return json.dumps(summary, ensure_ascii=False, indent=2), summary


def choose_entry_files(files: list[str]) -> list[str]:
    exact = [name for name in ENTRY_FILE_NAMES if name in files]
    nested_readmes = [path for path in files if Path(path).name.lower().startswith("readme.")]
    src_entries = [path for path in files if Path(path).name in {"main.py", "app.py", "index.js", "index.ts", "main.go", "main.rs"}]
    return dedupe([*exact, *nested_readmes, *src_entries])[:10]


def choose_test_commands(files: list[str]) -> list[str]:
    commands = [command for marker, command in TEST_COMMAND_CANDIDATES.items() if marker in files]
    if any(path.startswith("tests/") and path.endswith(".py") for path in files):
        commands.append("python -m pytest")
    return dedupe(commands)


def choose_top_level(files: list[str]) -> list[str]:
    names = [path.split("/", 1)[0] + ("/" if "/" in path else "") for path in files]
    return dedupe(names)[:30]


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
