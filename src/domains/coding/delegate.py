"""Orchestrator tool for one disposable, sandboxed coding delegation."""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import BaseTool, InjectedToolCallId, tool

from core.stream_events import StreamEventType
from agents.workers.result import WorkerResult
from domains.coding.agent import invoke_coding_agent
from domains.coding.settings import CodingConfig, load_coding_config
from agents.workers.lifecycle import create_worker_delegate, current_worker_invocation
from core.llm.config import LLMEndpoint
from project_workspace.store import ProjectStore
from domains.coding.run import CodingRunSession, CodingSourceArtifact, CodingExpectedOutput, CodingInputGrant
from domains.coding.budget import CodingTaskBudget
from domains.coding.sandbox.base import CodingSandboxFactory
from core.execution.sessions import make_session_key
from domains.coding.resources import (
    CodingResourcePool,
    get_coding_resource_pool,
)
from domains.coding.environments import (
    CodingEnvironmentRegistry,
    load_coding_environment_registry,
)


def make_delegate_coding_tool(
    endpoint: LLMEndpoint,
    *,
    project_store: ProjectStore | None = None,
    memory_endpoint: LLMEndpoint | None = None,
    environment_registry: CodingEnvironmentRegistry | None = None,
    coding_config: CodingConfig | None = None,
    resource_pool: CodingResourcePool | None = None,
    sandbox_factory: CodingSandboxFactory | None = None,
) -> BaseTool:
    """Create the project-bound ``delegate_coding`` tool."""
    coding_environments = environment_registry or load_coding_environment_registry()
    settings = coding_config or load_coding_config()
    coding_resources = resource_pool or get_coding_resource_pool(
        settings.total_compute_slots
    )
    @tool
    def delegate_coding(
        task: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        source_artifacts: list[CodingSourceArtifact] | None = None,
        input_grants: list[CodingInputGrant] | None = None,
        expected_outputs: list[CodingExpectedOutput | str] | None = None,
        environment: str | None = None,
        compute_slots: int | None = None,
        mpi_ranks: int | None = None,
        plan_id: str | None = None,
        step_id: str | None = None,
    ) -> WorkerResult:
        """Delegate a small general script, transformation, table, or custom plot.

        Prefer deterministic Structure/LAMMPS/Analysis delegates whenever they already cover
        the task. Coding has no external network, cannot see or edit the repository, and may
        read file inputs only when each exact completed-run artifact is listed in
        ``source_artifacts`` with run_id + role (+ artifact_id if that role is ambiguous).
        ``environment`` must be an alias returned by ``list_coding_environments``. Use
        ``lammps`` only for small custom LAMMPS code not covered by the specialist.
        Use ``ase`` for small Atomic Simulation Environment scripts outside existing specialist
        workflows, after confirming the runtime is available and its packages include ``ase``.
        ``compute_slots`` is the task's reservation from the coding-only total budget. Set
        ``mpi_ranks`` explicitly for bounded local MPI; it must fit within compute_slots and the
        selected environment must advertise ``mpi``. Omit MPI for ordinary or serial work.
        ``expected_outputs`` must specify exact workspace filenames (strings), or structured
        path/role/min_bytes/columns/required_keys contracts; a required key is a top-level JSON
        key or a dot path through nested objects (``pairs.P1``). Required outputs are host-verified.
        ``input_grants`` authorizes additional inputs from exact completed run IDs, constrained
        by roles and/or artifact IDs. The worker can request these without restarting; omitted
        grants allow only the initial staged files, never all project files.
        For an approved project plan, pass the exact plan_id and coding step_id.
        """
        invocation = current_worker_invocation()
        project_id, chat_thread_id = invocation.project_id, invocation.chat_thread_id
        tracker = invocation.tracker
        if project_store is None or project_id is None:
            return WorkerResult.fail(
                "Coding delegation requires an active persisted project."
            )
        normalized_task = str(task or "").strip()
        if not normalized_task:
            return WorkerResult.fail("Coding task must not be empty.")
        task_budget = CodingTaskBudget(settings.task_timeout_seconds)

        try:
            resolved_compute_slots, resolved_mpi_ranks = (
                settings.resolve_resource_request(
                    compute_slots=compute_slots,
                    mpi_ranks=mpi_ranks,
                )
            )
            selected_environment = coding_environments.resolve(environment)
            if (
                resolved_mpi_ranks > 1
                and "mpi" not in selected_environment.capabilities
            ):
                raise ValueError(
                    f"coding environment {selected_environment.alias!r} "
                    "does not support MPI"
                )
            invocation.emit(
                {
                    "type": StreamEventType.STATUS,
                    "message": (
                        "Waiting for coding resources: "
                        f"{resolved_compute_slots}/"
                        f"{settings.total_compute_slots} compute slot(s), "
                        f"{resolved_mpi_ranks} MPI rank(s)."
                    ),
                },
            )
            lease = coding_resources.acquire(
                compute_slots=resolved_compute_slots,
                mpi_ranks=resolved_mpi_ranks,
                session_key=(
                    make_session_key(project_id, chat_thread_id) if chat_thread_id else None
                ),
                task_budget=task_budget,
            )
        except Exception as exc:
            return WorkerResult.fail(
                str(exc), summary="Coding resources could not be allocated."
            )

        invocation.emit(
            {
                "type": StreamEventType.STATUS,
                "message": (
                    "Coding resources allocated: "
                    f"{lease.allocation.compute_slots} compute slot(s), "
                    f"{lease.allocation.mpi_ranks} MPI rank(s)."
                ),
            },
        )

        session: CodingRunSession | None = None
        try:
            session = CodingRunSession.prepare(
                store=project_store,
                project_id=project_id,
                task=normalized_task,
                source_artifacts=source_artifacts,
                expected_outputs=expected_outputs,
                chat_thread_id=chat_thread_id,
                runtime_environment=environment,
                environment_registry=coding_environments,
                config=settings,
                resource_allocation=lease.allocation,
                input_grants=input_grants,
                task_budget=task_budget,
                sandbox_factory=sandbox_factory,
            )
            tracker.record_run_ids([session.run_id])
        except Exception as exc:
            lease.release()
            if session is not None:
                try:
                    session.fail_if_running(str(exc))
                finally:
                    session.cleanup_ephemeral()
            return WorkerResult.fail(
                str(exc), summary="Coding sandbox could not be prepared."
            )
        assert session is not None
        try:
            worker_task = session.build_worker_task(normalized_task)
        except Exception as exc:
            lease.release()
            try:
                session.fail_if_running(str(exc))
            finally:
                session.cleanup_ephemeral()
            return WorkerResult.fail(
                str(exc), summary="Coding worker context could not be prepared."
            )

        try:
            result = invoke_coding_agent(
                endpoint,
                worker_task,
                reported_task=normalized_task,
                session=session,
                project_id=project_id,
                chat_thread_id=chat_thread_id,
                on_event=invocation.emit,
            )
        except Exception as exc:
            return WorkerResult.fail(
                str(exc), summary="Coding worker failed."
            )
        finally:
            lease.release()
        return result

    return create_worker_delegate(delegate_coding, agent="coding", project_store=project_store,
                                     memory_endpoint=memory_endpoint)


__all__ = ["make_delegate_coding_tool"]
