from dataclasses import dataclass


@dataclass(frozen=True)
class PlannedAction:
    tool: str
    arguments: dict
    description: str


class RuleBasedPlanner:
    """Small deterministic planner used before model-driven planning exists."""

    def select_skill(self, goal: str) -> str | None:
        normalized = goal.lower()
        if any(token in normalized for token in ("write test", "add test", "test case", "unit test")):
            return "test-writing"
        if any(token in normalized for token in ("review", "audit", "check diff", "code review")):
            return "code-review"
        if any(token in normalized for token in ("bug", "fail", "failing", "error", "pytest")):
            return "debugging"
        return None

    def plan_steps(self, goal: str) -> list[str]:
        return [
            "Inspect relevant files.",
            "Choose the smallest safe action.",
            "Verify the result with traceable evidence.",
        ]

    def next_action(self, goal: str, known_files: list[str]) -> PlannedAction:
        if "README.md" in known_files:
            return PlannedAction(
                tool="read_file",
                arguments={"path": "README.md"},
                description="Inspect README.md as a safe first context read.",
            )
        return PlannedAction(
            tool="list_files",
            arguments={},
            description="List workspace files because README.md is not present.",
        )
