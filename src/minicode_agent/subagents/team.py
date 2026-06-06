from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from minicode_agent.subagents.runner import SubagentRunner, diff_changed_files, status_changed_files
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
    worktree_path: str | None = None
    created_branch: str | None = None
    cleanup_policy: str = "manual"
    can_create_worktree: bool = False
    will_create_worktree: bool = False
    created_worktree: bool = False
    will_merge: bool = False
    reason: str


class PatchProposal(BaseModel):
    proposal_id: str
    base_branch: str | None = None
    worktree_path: str | None = None
    branch: str | None = None
    patch: str = ""
    changed_files: list[str] = Field(default_factory=list)
    test_result: dict | None = None
    risk_notes: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    will_merge: bool = False


class GitCommandResult(BaseModel):
    ok: bool
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


class TeamReport(BaseModel):
    team_id: str
    ok: bool
    findings: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    tool_summary: dict[str, int] = Field(default_factory=dict)
    risk_notes: list[str] = Field(default_factory=list)
    merge_blockers: list[str] = Field(default_factory=list)
    patch_proposals: list[PatchProposal] = Field(default_factory=list)


class TeamRun(BaseModel):
    team_id: str
    parent_run_id: str | None = None
    task: str
    roles: list[RoleProfile]
    workspace_plan: WorkspaceIsolationPlan
    results: list[SubagentResult] = Field(default_factory=list)
    report: TeamReport | None = None
    ok: bool = True
    failure_reason: str | None = None


class AgentTeam:
    """Central team orchestrator for bounded role workers.

    The central agent owns planning, worktree isolation, and result merging.
    Subagents run through bounded role profiles and never merge changes back.
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
        isolation_requested = any(request.role == SubagentRole.IMPLEMENTER for request in roles)
        worktree_manager = WorktreeManager(self.workspace, trace_store=self.trace_store, parent_run_id=self.parent_run_id)
        workspace_plan = worktree_manager.plan(requested=isolation_requested)
        if isolation_requested and workspace_plan.can_create_worktree:
            workspace_plan = worktree_manager.create(workspace_plan)
        team = TeamRun(
            team_id=f"team_{uuid4().hex[:8]}",
            parent_run_id=self.parent_run_id,
            task=task,
            roles=profiles,
            workspace_plan=workspace_plan,
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

        for request, profile in zip(roles, profiles, strict=True):
            self._trace(
                "team_role_started",
                {
                    "team_id": team.team_id,
                    "role": request.role.value,
                    "task": request.task,
                    "workspace": role_workspace(self.workspace, team.workspace_plan, request.role),
                    "profile": profile.model_dump(),
                },
            )
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
                    merge_blockers=["role is declared but not enabled"],
                    tool_calls=0,
                    stopped_reason="role_disabled",
                )
            elif request.role == SubagentRole.IMPLEMENTER and not team.workspace_plan.created_worktree:
                result = blocked_implementer_result(request, profile, team.workspace_plan)
            else:
                run_workspace = Path(role_workspace(self.workspace, team.workspace_plan, request.role))
                runner = SubagentRunner(run_workspace, trace_store=self.trace_store, parent_run_id=self.parent_run_id)
                result = runner.run(request)
                if request.role == SubagentRole.REVIEWER:
                    result = add_workspace_merge_context(result, team.workspace_plan)
                if request.role == SubagentRole.IMPLEMENTER:
                    proposal = collect_patch_proposal(run_workspace, team.workspace_plan)
                    result = result.model_copy(
                        update={
                            "summary": "implementer produced a patch proposal for central review.",
                            "evidence": [*result.evidence, *proposal.evidence],
                            "patch_proposal": proposal.model_dump(),
                            "merge_blockers": sorted(
                                set(
                                    [
                                        *result.merge_blockers,
                                        "patch proposal requires central review; automatic merge is disabled",
                                    ]
                                )
                            ),
                        }
                    )
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
                    "security_findings": result.security_findings,
                    "test_results": result.test_results,
                    "patch_proposal": result.patch_proposal,
                    "merge_blockers": result.merge_blockers,
                    "failure_reason": None if result.ok else result.summary,
                },
            )

        team.ok = all(result.ok for result in team.results)
        if not team.ok:
            team.failure_reason = "one or more team roles failed"
        team.results = attach_worktree_review_context(team.results, team.workspace_plan)
        team.report = build_team_report(team)
        if team.workspace_plan.created_worktree and team.workspace_plan.cleanup_policy == "manual":
            self._trace(
                "worktree_retained",
                {
                    "team_id": team.team_id,
                    "worktree_path": team.workspace_plan.worktree_path,
                    "created_branch": team.workspace_plan.created_branch,
                    "cleanup_policy": team.workspace_plan.cleanup_policy,
                    "reason": "isolated worktree retained for manual review of patch proposal artifacts",
                },
            )
        self._trace(
            "team_finished",
            {
                "team_id": team.team_id,
                "ok": team.ok,
                "roles": [result.role.value for result in team.results],
                "failure_reason": team.failure_reason,
                "team_workspace_plan": team.workspace_plan.model_dump(),
                "team_report": team.report.model_dump() if team.report else None,
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
    if role == SubagentRole.SECURITY_REVIEWER:
        return RoleProfile(
            role=role,
            allowed_tools=["git_diff", "git_status", "read_file", "search_code"],
            allowed_intents=["repo_inspect", "file_search", "file_read"],
            max_steps=max_steps,
            evidence_contract=EvidenceContract(
                required_fields=["security_findings", "evidence", "merge_blockers"]
            ),
        )
    if role == SubagentRole.TESTER:
        return RoleProfile(
            role=role,
            allowed_tools=["run_tests"],
            allowed_intents=["test_run"],
            max_steps=max_steps,
            max_risk="medium",
            evidence_contract=EvidenceContract(required_fields=["test_results", "evidence", "merge_blockers"]),
        )
    if role == SubagentRole.EXPLORER:
        return RoleProfile(
            role=role,
            allowed_tools=["list_files", "git_status", "read_file", "search_code"],
            allowed_intents=["repo_inspect", "file_search", "file_read"],
            max_steps=max_steps,
        )
    if role == SubagentRole.IMPLEMENTER:
        return RoleProfile(
            role=role,
            allowed_tools=[],
            allowed_intents=[],
            max_steps=max_steps,
            max_risk="medium",
            enabled=True,
            write_permission=False,
            evidence_contract=EvidenceContract(required_fields=["patch_proposal", "evidence", "merge_blockers"]),
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


class WorktreeManager:
    def __init__(
        self,
        workspace: Path,
        trace_store: TraceStore | None = None,
        parent_run_id: str | None = None,
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.trace_store = trace_store
        self.parent_run_id = parent_run_id

    def plan(self, requested: bool = False) -> WorkspaceIsolationPlan:
        return build_workspace_isolation_plan(self.workspace, requested=requested)

    def create(self, plan: WorkspaceIsolationPlan) -> WorkspaceIsolationPlan:
        if not plan.can_create_worktree or not plan.suggested_worktree_path:
            return plan
        branch = f"minicode-team-{uuid4().hex[:8]}"
        target = Path(plan.suggested_worktree_path)
        result = run_git(self.workspace, ["worktree", "add", "-b", branch, str(target), "HEAD"])
        if not result.ok:
            failed = plan.model_copy(
                update={
                    "will_create_worktree": False,
                    "created_worktree": False,
                    "created_branch": branch,
                    "worktree_path": str(target),
                    "reason": f"git worktree add failed: {result.error or result.stderr or 'unknown git error'}",
                }
            )
            self._trace("worktree_create_failed", failed.model_dump())
            return failed
        created = plan.model_copy(
            update={
                "will_create_worktree": True,
                "created_worktree": True,
                "created_branch": branch,
                "worktree_path": str(target),
                "reason": "clean git workspace isolated in a temporary worktree; automatic merge is disabled",
            }
        )
        self._trace("worktree_created", created.model_dump())
        return created

    def cleanup(self, plan: WorkspaceIsolationPlan) -> GitCommandResult:
        if not plan.worktree_path:
            return GitCommandResult(ok=True, exit_code=0, stdout="", stderr="", error=None)
        result = run_git(self.workspace, ["worktree", "remove", "--force", plan.worktree_path])
        self._trace(
            "worktree_cleanup_completed" if result.ok else "worktree_cleanup_failed",
            {"worktree_path": plan.worktree_path, "ok": result.ok, "error": result.error or result.stderr},
        )
        return result

    def _trace(self, event_type: str, payload: dict) -> None:
        if self.trace_store is None or self.parent_run_id is None:
            return
        self.trace_store.append(self.parent_run_id, event_type, payload)


def build_workspace_isolation_plan(workspace: Path, requested: bool = False) -> WorkspaceIsolationPlan:
    git = shutil.which("git")
    if not git:
        return WorkspaceIsolationPlan(
            git_available=False,
            repo_detected=False,
            reason="git executable is unavailable; cannot create worktrees or merge automatically.",
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
            reason="workspace is not a git repository; worktree isolation is unavailable.",
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
    dirty_paths = [
        line[3:]
        for line in status.splitlines()
        if len(line) > 3 and not line[3:].replace("\\", "/").startswith(".minicode/")
    ]
    suggested = workspace.parent / f"{workspace.name}-team-{uuid4().hex[:8]}"
    dirty = bool(dirty_paths)
    return WorkspaceIsolationPlan(
        git_available=True,
        repo_detected=True,
        current_branch=branch,
        dirty=dirty,
        dirty_paths=dirty_paths,
        suggested_worktree_path=str(suggested),
        can_create_worktree=requested and not dirty,
        will_create_worktree=requested and not dirty,
        will_merge=False,
        reason=(
            "dirty worktree detected; worktree isolation is blocked to avoid hiding or overwriting user changes"
            if dirty
            else (
                "clean git workspace detected; worktree isolation can be created and automatic merge is disabled"
                if requested
                else "clean git workspace detected; no isolated implementer task requested"
            )
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
    blockers.append("automatic merge is disabled; central agent must review any patch proposal")
    evidence = list(result.evidence)
    evidence.append(
        {
            "type": "workspace_plan",
            "dirty": plan.dirty,
            "branch": plan.current_branch,
            "worktree_path": plan.worktree_path,
            "created_worktree": plan.created_worktree,
        }
    )
    return result.model_copy(update={"merge_blockers": sorted(set(blockers)), "risks": sorted(set(risks)), "evidence": evidence})


def blocked_implementer_result(
    request: SubagentRequest,
    profile: RoleProfile,
    plan: WorkspaceIsolationPlan,
) -> SubagentResult:
    blocker = plan.reason or "worktree isolation is unavailable"
    return SubagentResult(
        role=request.role,
        task=request.task,
        ok=False,
        summary="implementer requires an isolated clean git worktree before producing a patch proposal.",
        findings=[],
        evidence=[{"type": "workspace_plan", **plan.model_dump()}],
        allowed_tools=profile.allowed_tools,
        denied_tools=[],
        changed_files=[],
        risks=["Implementer was blocked before any write-capable action could run."],
        test_suggestions=[],
        merge_blockers=[blocker],
        tool_calls=0,
        stopped_reason="worktree_blocked",
    )


def role_workspace(workspace: Path, plan: WorkspaceIsolationPlan, role: SubagentRole) -> str:
    if plan.created_worktree and role in {
        SubagentRole.IMPLEMENTER,
        SubagentRole.REVIEWER,
        SubagentRole.SECURITY_REVIEWER,
        SubagentRole.TESTER,
    }:
        return plan.worktree_path or str(workspace)
    return str(workspace)


def collect_patch_proposal(workspace: Path, plan: WorkspaceIsolationPlan) -> PatchProposal:
    proposal_id = f"proposal_{uuid4().hex[:8]}"
    diff = run_git(workspace, ["diff", "--binary"])
    status = run_git(workspace, ["status", "--short"])
    patch = diff.stdout if diff.ok else ""
    changed_files = sorted(set(diff_changed_files(patch) | status_changed_files(status.stdout if status.ok else "")))
    risk_notes: list[str] = []
    if not changed_files:
        risk_notes.append("Patch proposal is empty; implementer made no file changes.")
    if not diff.ok:
        risk_notes.append(f"Could not collect git diff: {diff.error or diff.stderr or 'unknown git error'}")
    evidence = [
        {
            "type": "patch_proposal",
            "proposal_id": proposal_id,
            "tool": "git_diff",
            "ok": diff.ok,
            "output_chars": len(patch),
            "changed_files": changed_files,
        },
        {
            "type": "workspace_plan",
            "dirty": plan.dirty,
            "branch": plan.current_branch,
            "worktree_path": plan.worktree_path,
            "created_branch": plan.created_branch,
            "created_worktree": plan.created_worktree,
        },
    ]
    return PatchProposal(
        proposal_id=proposal_id,
        base_branch=plan.current_branch,
        worktree_path=plan.worktree_path or str(workspace),
        branch=plan.created_branch,
        patch=patch,
        changed_files=changed_files,
        test_result=None,
        risk_notes=risk_notes,
        evidence=evidence,
        will_merge=False,
    )


def attach_worktree_review_context(results: list[SubagentResult], plan: WorkspaceIsolationPlan) -> list[SubagentResult]:
    proposal = next((result.patch_proposal for result in results if result.patch_proposal), None)
    if not proposal:
        return results
    test_result = next((result.test_results[0] for result in results if result.test_results), None)
    updated: list[SubagentResult] = []
    for result in results:
        if result.patch_proposal:
            patched = dict(result.patch_proposal)
            patched["test_result"] = test_result
            updated.append(result.model_copy(update={"patch_proposal": patched}))
            continue
        if result.role in {SubagentRole.REVIEWER, SubagentRole.SECURITY_REVIEWER, SubagentRole.TESTER}:
            evidence = [
                *result.evidence,
                {
                    "type": "worktree_review_context",
                    "proposal_id": proposal.get("proposal_id"),
                    "worktree_path": plan.worktree_path,
                    "created_branch": plan.created_branch,
                    "role": result.role.value,
                },
            ]
            updated.append(result.model_copy(update={"evidence": evidence}))
            continue
        updated.append(result)
    return updated


def build_team_report(team: TeamRun) -> TeamReport:
    tool_summary: dict[str, int] = {}
    findings: list[str] = []
    evidence: list[dict] = []
    risk_notes: list[str] = []
    merge_blockers: list[str] = []
    patch_proposals: list[PatchProposal] = []
    for result in team.results:
        findings.extend(result.findings)
        evidence.extend(result.evidence)
        risk_notes.extend(result.risks)
        merge_blockers.extend(result.merge_blockers)
        tool_summary[result.role.value] = tool_summary.get(result.role.value, 0) + result.tool_calls
        if result.patch_proposal:
            patch_proposals.append(PatchProposal.model_validate(result.patch_proposal))
    return TeamReport(
        team_id=team.team_id,
        ok=team.ok,
        findings=findings,
        evidence=evidence,
        tool_summary=tool_summary,
        risk_notes=sorted(set(risk_notes)),
        merge_blockers=sorted(set(merge_blockers)),
        patch_proposals=patch_proposals,
    )


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
