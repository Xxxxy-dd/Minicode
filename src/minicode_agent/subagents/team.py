from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from minicode_agent.subagents.runner import SubagentRunner
from minicode_agent.subagents.types import SubagentRequest, SubagentResult, SubagentRole
from minicode_agent.trace import TraceStore


class EvidenceContract(BaseModel):
    required_fields: list[str] = Field(default_factory=lambda: ["summary", "findings", "evidence"])
    require_structured_evidence: bool = True


class RoleProfile(BaseModel):
    role: SubagentRole
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_intents: list[str] = Field(default_factory=list)
    max_steps: int = 3
    max_risk: str = "low"
    path_scope: str = "."
    evidence_contract: EvidenceContract = Field(default_factory=EvidenceContract)
    enabled: bool = True
    write_permission: bool = False

    @field_validator("max_steps")
    @classmethod
    def validate_max_steps(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_steps must be at least 1")
        return min(value, 5)


class WorkspaceIsolationPlan(BaseModel):
    git_available: bool
    repo_detected: bool
    current_branch: str | None = None
    dirty: bool = False
    dirty_paths: list[str] = Field(default_factory=list)
    suggested_worktree_path: str | None = None
    can_create_worktree: bool = False
    will_create_worktree: bool = False
    will_merge: bool = False
    reason: str


class GitCommandResult(BaseModel):
    ok: bool
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


class TeamRun(BaseModel):
    team_id: str
    parent_run_id: str | None = None
    task: str
    roles: list[RoleProfile]
    workspace_plan: WorkspaceIsolationPlan
    results: list[SubagentResult] = Field(default_factory=list)
    ok: bool = True
    failure_reason: str | None = None


class AgentTeam:
    """Central team orchestrator for bounded role workers.

    V1.1 keeps this deliberately sequential and read-only. It records the
    protocol and evidence shape without creating worktrees or merging changes.
    """

    def __init__(
        self,
        workspace: Path,
        trace_store: TraceStore | None = None,
        parent_run_id: str | None = None,
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.trace_store = trace_store
        self.parent_run_id = parent_run_id

    def run(self, task: str, roles: list[SubagentRequest]) -> TeamRun:
        profiles = [role_profile_for(request.role, request.max_steps) for request in roles]
        team = TeamRun(
            team_id=f"team_{uuid4().hex[:8]}",
            parent_run_id=self.parent_run_id,
            task=task,
            roles=profiles,
            workspace_plan=build_workspace_isolation_plan(self.workspace),
        )
        self._trace(
            "team_started",
            {
                "team_id": team.team_id,
                "parent_run_id": self.parent_run_id,
                "task": task,
                "roles": [profile.model_dump() for profile in profiles],
                "team_workspace_plan": team.workspace_plan.model_dump(),
            },
        )

        runner = SubagentRunner(self.workspace, trace_store=self.trace_store, parent_run_id=self.parent_run_id)
        for request, profile in zip(roles, profiles, strict=True):
            if not profile.enabled:
                result = SubagentResult(
                    role=request.role,
                    task=request.task,
                    ok=False,
                    summary=f"{request.role.value} role is declared but not enabled in V1.1.",
                    findings=[],
                    evidence=[],
                    allowed_tools=profile.allowed_tools,
                    denied_tools=[],
                    changed_files=[],
                    risks=[],
                    test_suggestions=[],
                    merge_blockers=["implementer role has no independent write permission in V1.1"],
                    tool_calls=0,
                    stopped_reason="role_disabled",
                )
            else:
                result = runner.run(request)
                if request.role == SubagentRole.REVIEWER:
                    result = add_workspace_merge_context(result, team.workspace_plan)
            team.results.append(result)
            self._trace(
                "team_role_completed",
                {
                    "team_id": team.team_id,
                    "role": request.role.value,
                    "task": request.task,
                    "ok": result.ok,
                    "summary": result.summary,
                    "tool_calls": result.tool_calls,
                    "evidence_refs": summarize_evidence(result.evidence),
                    "merge_blockers": result.merge_blockers,
                    "failure_reason": None if result.ok else result.summary,
                },
            )

        team.ok = all(result.ok for result in team.results)
        if not team.ok:
            team.failure_reason = "one or more team roles failed"
        self._trace(
            "team_finished",
            {
                "team_id": team.team_id,
                "ok": team.ok,
                "roles": [result.role.value for result in team.results],
                "failure_reason": team.failure_reason,
                "team_workspace_plan": team.workspace_plan.model_dump(),
            },
        )
        return team

    def _trace(self, event_type: str, payload: dict) -> None:
        if self.trace_store is None or self.parent_run_id is None:
            return
        self.trace_store.append(self.parent_run_id, event_type, payload)


def role_profile_for(role: SubagentRole, max_steps: int = 3) -> RoleProfile:
    if role == SubagentRole.REVIEWER:
        return RoleProfile(
            role=role,
            allowed_tools=["git_diff", "git_status", "read_file", "search_code"],
            allowed_intents=["repo_inspect", "file_search", "file_read"],
            max_steps=max_steps,
            evidence_contract=EvidenceContract(
                required_fields=["changed_files", "risks", "test_suggestions", "evidence", "merge_blockers"]
            ),
        )
    if role == SubagentRole.EXPLORER:
        return RoleProfile(
            role=role,
            allowed_tools=["list_files", "git_status", "read_file", "search_code"],
            allowed_intents=["repo_inspect", "file_search", "file_read"],
            max_steps=max_steps,
        )
    return RoleProfile(
        role=role,
        allowed_tools=[],
        allowed_intents=[],
        max_steps=max_steps,
        enabled=False,
        write_permission=False,
        evidence_contract=EvidenceContract(required_fields=["merge_blockers"]),
    )


def build_workspace_isolation_plan(workspace: Path) -> WorkspaceIsolationPlan:
    git = shutil.which("git")
    if not git:
        return WorkspaceIsolationPlan(
            git_available=False,
            repo_detected=False,
            reason="git executable is unavailable; V1.1 will not create worktrees or merge automatically.",
        )
    inside = run_git(workspace, ["rev-parse", "--is-inside-work-tree"])
    if not inside.ok:
        return WorkspaceIsolationPlan(
            git_available=True,
            repo_detected=False,
            reason=f"git repository detection failed: {inside.error or inside.stderr or 'unknown git error'}",
        )
    if inside.stdout.strip() != "true":
        return WorkspaceIsolationPlan(
            git_available=True,
            repo_detected=False,
            reason="workspace is not a git repository; V1.1 records only the team plan.",
        )

    branch_result = run_git(workspace, ["branch", "--show-current"])
    status_result = run_git(workspace, ["status", "--short"])
    if not status_result.ok:
        return WorkspaceIsolationPlan(
            git_available=True,
            repo_detected=True,
            current_branch=branch_result.stdout.strip() or None if branch_result.ok else None,
            reason=f"git status failed: {status_result.error or status_result.stderr or 'unknown git error'}",
        )
    branch = branch_result.stdout.strip() or None if branch_result.ok else None
    status = status_result.stdout
    dirty_paths = [line[3:] for line in status.splitlines() if len(line) > 3]
    suggested = workspace.parent / f"{workspace.name}-team-{branch or 'detached'}"
    return WorkspaceIsolationPlan(
        git_available=True,
        repo_detected=True,
        current_branch=branch,
        dirty=bool(status.strip()),
        dirty_paths=dirty_paths,
        suggested_worktree_path=str(suggested),
        can_create_worktree=not bool(status.strip()),
        will_create_worktree=False,
        will_merge=False,
        reason=(
            "dirty worktree detected; reviewer must flag merge risk and V1.1 will not create or merge worktrees"
            if status.strip()
            else "clean git workspace detected; V1.1 still records only a future worktree plan"
        ),
    )


def run_git(workspace: Path, args: list[str]) -> GitCommandResult:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except OSError as exc:
        return GitCommandResult(ok=False, exit_code=None, error=str(exc))
    except subprocess.TimeoutExpired as exc:
        return GitCommandResult(
            ok=False,
            exit_code=None,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            error="git command timed out",
        )
    return GitCommandResult(
        ok=completed.returncode == 0,
        exit_code=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
    )


def add_workspace_merge_context(result: SubagentResult, plan: WorkspaceIsolationPlan) -> SubagentResult:
    blockers = list(result.merge_blockers)
    risks = list(result.risks)
    if plan.dirty:
        blockers.append("workspace has uncommitted changes; do not auto-merge or overwrite user work")
        risks.append("Dirty worktree increases merge risk for reviewer findings.")
    blockers.append("V1.1 does not create worktrees or perform automatic merges.")
    evidence = list(result.evidence)
    evidence.append({"type": "workspace_plan", "dirty": plan.dirty, "branch": plan.current_branch})
    return result.model_copy(update={"merge_blockers": sorted(set(blockers)), "risks": sorted(set(risks)), "evidence": evidence})


def summarize_evidence(evidence: list[dict]) -> list[dict]:
    summary: list[dict] = []
    for item in evidence:
        summary.append(
            {
                "type": item.get("type"),
                "tool": item.get("tool"),
                "ok": item.get("ok"),
                "output_chars": item.get("output_chars"),
                "error": item.get("error"),
                "dirty": item.get("dirty"),
                "branch": item.get("branch"),
            }
        )
    return [{key: value for key, value in item.items() if value is not None} for item in summary]
