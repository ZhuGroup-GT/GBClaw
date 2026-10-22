# Extension roadmap

[Documentation](README.md) · [中文](zh/roadmap.md) · [Architecture and design](architecture.md)

These are possible extensions for larger investigations and deployments, not release commitments or a list of verified defects. The linked guides describe the supported behavior; confirm the implementation and task requirements before starting an extension.

## Verified reuse across workflows

CSL already supports exact, verified reuse. Other scientific workflows rely on the agent to compare retrieved candidates. A shared check before execution could compare input content, potential content, cell and boundary conditions, fixed atoms, reference structures, scientific parameters and algorithm versions. A source-file hash identifies bytes; it does not by itself establish equivalent calculations.

Return candidate run and artifact IDs, parameter differences, integrity checks and convergence status, together with the reason for reuse or recomputation. Completion alone does not prove convergence. A verified reuse path for Coding would also need an explicit completion contract before creating a new sandbox. An explicit request to recompute should take precedence.

See [project memory](project-memory.md) and [extension guide](extension-guide.md).

## Discoverable batch results

Extend candidate discovery with consistent item, frame, constraint and reference filters, plus common scientific terms in both English and Chinese. A successful item from a partially failed batch needs independently validated outputs and exact source references before it can be offered for reuse; the parent batch must retain its actual status.

Keep retrieval previews bounded and expose omitted candidates through pagination and exact queries. Record why a candidate was selected or rejected so that missing records, search gaps and scientific differences can be distinguished without expanding every agent's context.

See [memory query tools](project-memory.md#agent-query-tools).

## Coding continuation and scientific checks

Input additions already preserve scratch work within an invocation. Durable continuation could export selected source files, progress and necessary failure details, then restore those snapshots into a fresh sandbox. Drafts and failed outputs must remain distinct from validated results, and resumed work must retain exact input authorization and provenance.

Further extensions could provide workspace-only image previews, versioned scientific recipes and domain-specific output checks. Existing format checks and successful script execution cannot establish scientific correctness; numerical invariants, units and input-to-output relationships need explicit validation contracts. Repeated, stable recipes can become deterministic tools.

See [Coding agent](coding-agent.md) and [tool integration](extension-guide.md).

## Resource coordination across service processes

Coding and LAMMPS admission pools currently account for resources within each service process. A shared scheduler or allocation store could enforce a host-wide budget across workers and both execution paths, with cancellation-aware waiting and owner-bound release.

Process-tree limits remain a separate responsibility: distinguish delegated cgroup enforcement from sampling, and define cleanup when a service process dies or a child detaches. Extending admission must preserve project ownership and prevent a late worker from publishing after its execution has ended.

See [Coding resource enforcement](coding-agent.md#process-tree-resource-enforcement) and [API execution lifecycle](api-and-ui.md).

## Evidence identity and physical units

New permanent artifacts already receive content hashes. A migration for older files should distinguish an original recorded digest from a newly measured digest: checking today's bytes cannot prove their historical identity. Apply consistent source verification to additional specialized input paths, including the exact potential file used during execution.

External structures also need explicit length-unit declarations and controlled conversion. An imported-coordinate limit is only an angstrom limit when the source units establish that equivalence. Preserve original physical values and record any conversion or display scaling separately.

If finer file visibility is introduced, apply it consistently to search, handoff, direct reads and archived tool replies. Retention labels alone do not define access rights.

See [artifacts and provenance](project-memory.md#artifact-manifests-and-provenance) and [analysis tools](tools.md).

## Archive capacity and maintenance

Add storage usage reporting and reference tracking for full tool outputs, runtime checkpoints and derived views. A maintenance policy should identify which records are still referenced before reclaiming unreferenced temporary or derived data; age alone is insufficient.

Keep authoritative facts, runs, conclusions, scientific artifacts and historical summaries available. Summary compression must not delete this evidence. Regenerating views, compacting runtime storage and exporting project archives should preserve stable IDs and exact lookup paths.

See [summary history](project-memory.md#historical-summaries-and-lookup-pointers) and [runtime checkpoints](project-memory.md#runtime-checkpoints).

## Reconnection and execution visibility

Expose project occupancy and cleanup progress more clearly, and consider bounded reconnect attempts after a connection loss. Reconnection should consult existing request IDs and durable results, acquire valid ownership and wait for the previous execution to exit before admitting new work. It must not silently replay a scientific calculation or weaken the one-frontend-per-project rule.

See [Web API and interface](api-and-ui.md).
