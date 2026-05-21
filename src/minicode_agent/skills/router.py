import re
from dataclasses import dataclass, field

from minicode_agent.skills.registry import SkillDefinition, SkillRegistry


@dataclass(frozen=True)
class SkillRouteWeights:
    name: int = 4
    aliases: int = 4
    tags: int = 3
    applies_to: int = 3
    examples: int = 2
    description: int = 1


@dataclass(frozen=True)
class SkillRouteCandidate:
    name: str
    score: int
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SkillRouteResult:
    selected: list[str]
    candidates: list[SkillRouteCandidate]

    @property
    def reasons(self) -> dict[str, list[str]]:
        return {candidate.name: candidate.reasons for candidate in self.candidates if candidate.name in self.selected}


class SkillRouter:
    """Deterministic metadata-based skill router."""

    def __init__(
        self,
        registry: SkillRegistry | None = None,
        top_k: int = 2,
        weights: SkillRouteWeights | None = None,
        min_score_gap: int = 5,
    ) -> None:
        self.registry = registry or SkillRegistry()
        self.top_k = top_k
        self.weights = weights or SkillRouteWeights()
        self.min_score_gap = min_score_gap

    def route(self, task: str) -> SkillRouteResult:
        tokens = tokenize(task)
        direct_skills = direct_skill_aliases(task)
        candidates = [score_skill(skill, tokens, task, self.weights, direct_skills) for skill in self.registry.list()]
        candidates = [candidate for candidate in candidates if candidate.score > 0]
        candidates.sort(key=lambda candidate: (-candidate.score, candidate.name))
        selected = select_top_candidates(candidates, self.top_k, self.min_score_gap)
        return SkillRouteResult(selected=selected, candidates=candidates)


def score_skill(
    skill: SkillDefinition,
    task_tokens: set[str],
    task: str,
    weights: SkillRouteWeights,
    direct_skills: set[str] | None = None,
) -> SkillRouteCandidate:
    score = 0
    reasons: list[str] = []
    direct_skills = direct_skills or set()
    if skill.name in direct_skills:
        score += weights.aliases * 3
        reasons.append("direct alias matched task text")
    fields = {
        "name": [skill.name],
        "aliases": skill.metadata.aliases,
        "tags": skill.metadata.tags,
        "applies_to": skill.metadata.applies_to,
        "examples": skill.metadata.examples,
        "description": [skill.metadata.description],
    }
    for field_name, values in fields.items():
        for value in values:
            matched = sorted(tokenize(value) & task_tokens)
            phrase_match = value.lower() in task.lower()
            if matched or phrase_match:
                field_weight = getattr(weights, field_name)
                amount = field_weight * max(1, len(matched))
                if phrase_match:
                    amount += field_weight
                score += amount
                reasons.append(f"{field_name} matched {', '.join(matched) or value}")
    return SkillRouteCandidate(name=skill.name, score=score, reasons=reasons)


def select_top_candidates(candidates: list[SkillRouteCandidate], top_k: int, min_score_gap: int) -> list[str]:
    if not candidates:
        return []
    selected = [candidates[0].name]
    for candidate in candidates[1:top_k]:
        if candidates[0].score - candidate.score <= min_score_gap:
            selected.append(candidate.name)
    return selected


def tokenize(text: str) -> set[str]:
    stop_words = {
        "and",
        "for",
        "the",
        "this",
        "that",
        "with",
        "project",
        "code",
        "task",
        "skill",
        "use",
        "when",
    }
    words = {
        word
        for word in re.findall(r"[a-zA-Z0-9]+", text.lower())
        if len(word) >= 3 and word not in stop_words
    }
    aliases = {
        "pytest": {"test", "tests", "failing", "failure"},
        "diff": {"review", "audit"},
        "traceback": {"bug", "error", "failure"},
    }
    expanded = set(words)
    for word in words:
        expanded.update(aliases.get(word, set()))
    return expanded


def direct_skill_aliases(text: str) -> set[str]:
    mapping = {
        "修复": {"debugging"},
        "报错": {"debugging"},
        "错误": {"debugging"},
        "失败": {"debugging"},
        "测试": {"debugging", "test-writing"},
        "单测": {"test-writing"},
        "覆盖": {"test-writing"},
        "审查": {"code-review"},
        "评审": {"code-review"},
        "检查diff": {"code-review"},
        "代码审查": {"code-review"},
    }
    matched: set[str] = set()
    normalized = text.replace(" ", "").lower()
    for phrase, skills in mapping.items():
        if phrase in normalized:
            matched.update(skills)
    return matched
