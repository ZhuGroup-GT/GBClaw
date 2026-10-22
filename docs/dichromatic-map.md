# Interactive DichromaticMap cells

A dichromatic map overlays sites from two differently oriented grains. It helps a researcher inspect near-coincidence and choose a projected cell for further geometric analysis. In GBclaw, the Structure agent prepares candidate sites, the researcher selects four vertices, and the backend computes the strain needed to align the selected cell.

This page describes that interaction and what its results mean. For how it fits into crystallographic screening and later simulations, see [Architecture and design](architecture.md) and the [Structure tool reference](tools.md#dichromatic-maps).

## Design rationale

Near-CSL investigation can involve several plausible cells with different sizes and strain requirements. Keeping the selection visible lets the researcher choose the geometry relevant to the scientific question. The agent prepares the candidates and reports measurements; the confirmation step records which cell the researcher actually chose.

The browser and backend exchange numerical coordinates and stable candidate IDs. This preserves the exact selection when the view is zoomed, the session is resumed, or the calculation is reviewed later. A PNG is a useful record of the result, while the numerical selection and saved transformations carry the reproducible geometry.

Preparation, confirmation, and strain application have separate states and records. Opening a picker therefore cannot be mistaken for a completed cell calculation, and refreshing the page cannot silently launch one. This follows the wider architecture's separation between proposing work, researcher decisions, and recorded execution.

## What to ask for

For an existing completed, unstrained map, a request can be as simple as:

> Open the cell picker for this dichromatic-map run so I can select a near-CSL cell. After I confirm the four vertices, report the fitted strain, residuals, and candidate edge vectors.

For a new map, specify the lattice, tilt axis, angle, and lattice constant, along with the question you want to investigate. The agent can query the supported options before preparing the picker. The workflow below explains what happens after preparation.

## Interaction

1. Ask the agent for initial dichromatic/near-CSL setup or to open the picker. Initial setup uses the interactive widget; a PNG-only export is reserved for explicitly requested static figures. Preparing the window does not choose points or apply strain. `prepare_dichromatic_cell_selection` saves a project-scoped request and returns an `interaction` reference. Supply the lattice, actual signed axis/angle, a0, layers, cutoff, view and purpose. To reuse a completed unstrained map, omit `parameters` and pass that map's `source_run_id`; the tool restores its saved settings and retains the source relationship; no PNG export is needed.
2. The floating widget automatically opens with those parameters already filled in, including axis-specific presets, angle range, and layers. Drag or zoom to inspect more atoms; the viewport API returns coordinates for the new visible region. Physical geometry stays fixed during picking.
3. Click four highlighted paired sites in one axial layer, following the perimeter of the intended cell. The widget numbers them and supports undo/reset. Marker sizes and symbols use defaults rather than exposing additional appearance controls.
4. Click **Confirm cell and continue**. The browser submits the four candidate IDs with the current request revision. The server checks that they belong to the request and form a valid cell, then saves the selection. Invalid geometry remains editable and reports its error.
5. The next chat turn passes the request ID to Structure. It first calls `get_dichromatic_cell_selection`, then `apply_dichromatic_cell_strain` for a selected request. The strain calculation uses the saved numerical selection and returns the transformed map, cell, strain/rotation information, and residuals. An already applied request reuses its result.

The continuation is triggered by the user's confirmation button. If a chat turn is still running, the browser waits for it to finish before sending the continuation. Switching projects does not transfer a request or queued continuation to the other project. Minimizing the window keeps its state. After a reload, project lookup restores both pending requests and confirmed selections whose strain has not yet been applied. A restored selection provides a **Continue with agent** button; refreshing the page does not start or retry the strain solver.

After strain is applied, the widget displays the aligned numerical map. Panning or zooming rebuilds the visible atoms using the saved deformation gradients F and translations. It neither reruns the strain solver nor switches the view back to the original unstrained grains. The tilt-axis menu provides common axes and a **Custom axis…** entry for explicit Miller indices. Changing the axis refreshes its angle presets and layers before rendering a new map.

## State and scientific meaning

The request state tells you which part of the interaction is complete:

| State | Meaning |
| --- | --- |
| `pending` | Candidate sites are ready; the researcher has not confirmed a cell. |
| `selected` | A valid four-vertex selection has been confirmed and saved. |
| `applying` | The selected cell's strain calculation is in progress. |
| `applied` | The strain result is recorded and can be reused. |
| `cancelled` | A pending request was cancelled. |

Applying a pending or cancelled request is rejected. A stale browser revision cannot replace a newer selection. Existing applied results are returned for identical settings; changing settings requires a new request rather than silently overwriting the result.

A completed preparation delegation means that the candidates were prepared, not that the user's cell was chosen. If the work belongs to a project plan, the step remains blocked while selection or strain is unfinished, and the continuation carries its original plan and step IDs. A failed calculation still marks the delegated work as failed.

All projected lengths use a0; `lattice_constant` specifies its Angstrom conversion. Candidate proximity does not prove an exact unstrained CSL. The explicit strain operation aligns the selected projected cell within the supplied strain/rotation bounds. It does not establish a primitive three-dimensional cell, generate a relaxed atomic boundary, or calculate its energy.

## Edge vectors after strain

The edge measurement asks a specific geometric question: along each selected cell edge, what is the shortest nonzero separation between sites from the two grains in the same axial layer? It uses saved atom identities and transformations, so the answer is independent of the current viewport or visible layers.

The search covers each of the four complete, finite **selected polygon edges**. It checks same-layer G1/G2 pairs in **every axial layer**, then retains each edge's minimum across layers and all pairs tied at that distance. Atoms must lie on the segment; the search does not widen it into a strip or mix atoms from different layers. Coincident zero-distance pairs are excluded. Edges with fully coincident atomic rows yield inter-site translations, explicitly marked as having no mismatch evidence.

`edge_analysis` records per-layer results, per-edge global minima, counts, atom identities, layers, directed G1→G2 displacements, and both grain representations from the library's Measure vector functions. `lattice_notation` uses each grain's **deformed conventional lattice basis**, while `current_components_a0` uses its polar-rotated orthonormal frame. Simple fractions are shown only when they reproduce the components within the recorded tolerance; other results retain `≈`, numerical components and reconstruction errors. These are geometric candidate secondary-misfit Burgers vectors, not identified defects or a Burgers-circuit result.

PNG and browser views support multiple numbered arrows (`E1.1`, `E1.2`, …), paired with an edge legend showing G1/G2 notation and a0/Angstrom lengths. Arrows have their actual length; only labels are offset. The widget can hide annotations or collapse the legend. Panning uses the saved measurements and only transforms their display coordinates. PNG exports use a square image and a square data region. The selected cell is centered and fitted with a margin; atoms are regenerated for that entire region using the saved strain. Exporting does not stretch or crop the interactive view, change the selection, or rerun strain fitting. The JSON records this export viewport separately from the original view.

For an existing applied request without measurements across all layers, call `analyze_dichromatic_cell_edges(request_id)` or POST its `/edge-analysis` endpoint. This creates a derived run linked to the original strain run without refitting or changing that original. Current all-layer analysis is reused; historical selected-layer results are retained as source evidence rather than reused as all-layer minima. Complete vectors and annotations stay in `dichromatic_map.json`; large agent/memory responses carry explicitly marked previews and reference that full artifact. The bounded `edge_vector_summary` keeps the minimum distances, winning layers, both signed grain notations, tie counts and scientific qualifications. It is passed through the Structure delegate to the user-facing agent, which must report these candidate vectors rather than only strain metrics. Analysis limits produce an explicit incomplete status, never an unreported subset of tied minima.

## Persistence and extension points

Pending request records live under the current project's `runtime/dichromatic_requests/`. Preserve that directory when moving a project with unfinished interactive work. Confirming the four points creates a completed `dichromatic_map` run with the `dichromatic_cell_selection` artifact. Applying strain creates another completed run with that selection run as its source, plus PNG and numerical JSON artifacts.

The registry's `select_dichromatic_cell`, `apply_dichromatic_cell_strain` and `analyze_dichromatic_cell_edges` publishers retain the request ID, purpose, selection, scientific outputs, and source relationships in memory; `render_dichromatic_map` publishes static exports. Full point arrays stay in artifacts. Pending requests and read-only previews do not masquerade as completed scientific results. Publication failures use the existing memory recovery process.

Relevant implementation points:

- `src/domains/structure/dichromatic/vectors.py`: exact finite-edge pairing and grain-vector coordinates.
- `src/domains/structure/dichromatic/adapter.py`: coordinates, stable candidate IDs, cell validation, and numerical strain application using the installed library.
- `src/domains/structure/dichromatic/interactions.py`: project ownership, request revisions, user selections, plan references, and linked scientific runs.
- `src/domains/structure/routes.py`: options, numerical previews, static export, and request list/selection/cancel/apply/edge-analysis endpoints, mounted by the API through the `routers` declaration in `src/domains/structure/extension.py`.
- `src/domains/structure/dichromatic/tools.py`: agent options, static export (`render_dichromatic_map`, shared with the export endpoint), preparation, lookup, strain and edge-analysis tools.
- `src/agents/streaming/events.py`: compact `interactions` references extracted before output previews are truncated; `src/domains/structure/delegate.py` retains them in its parent result.
- `web/assets/js/dichromatic_widget.js`: canvas rendering, viewport requests, picking, and explicit chat continuation.

See [API and UI](api-and-ui.md#dichromaticmap-api-and-widget) for endpoint details and [extension guide](extension-guide.md) for the publication contract.
