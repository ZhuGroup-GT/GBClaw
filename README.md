# GBClaw DemoProj showcase

This branch contains the public, read-only GBClaw demonstration and its recorded
`projects/DemoProj` research workspace. It follows an FCC Ni `(110) ∥ (111)` tilt
boundary study through cell selection, minimization, shear loading, reverse
unloading, and structural analysis.

The website includes the original conversation, execution traces loaded on
expansion, project memory, a file browser with previews and downloads, recorded
figures, and interactive views of two saved dichromatic maps. Map controls explore
saved coordinates; they do not compute new structures. Live chat, project editing,
and new calculations are unavailable in this demonstration.

## Preview locally

From the repository root:

```sh
python3 -m http.server 8000 --bind 127.0.0.1
```

Open `http://localhost:8000/`. Serve the repository root so that both `html/` and
`projects/DemoProj/` are accessible. The website uses browser modules and HTTP
requests, so opening `index.html` directly as a local file is insufficient.

## Static hosting

Publish the repository root, preserving the relative paths of `html/` and
`projects/DemoProj/`. The root entry point redirects to `html/`; all data and file
URLs work under a repository subpath. `.nojekyll` allows ordinary static serving
without transforming the recorded workspace.

No research backend or API credentials are required. The private development
repository is not included. This implementation has not been deployed by the
local development changes.

## Refresh the recorded data

After updating DemoProj, regenerate its browsing snapshots:

```sh
python3 html/scripts/build_demo.py
```

The standard-library script reads the existing SQLite databases in immutable,
read-only mode and generates `html/data/demo.json`, `history.json`, and lazy turn
details. It indexes original project files without copying them. Caches, hidden
entries, lock files, and SQLite sidecars are omitted from the browser index.
Seven large tool outputs in the current snapshot are marked as shortened display
previews; original transcripts and numerical artifacts remain downloadable.
Text file previews are limited to the first 256 KB, with full-file downloads
available alongside them.

## Validation

```sh
node html/tests/demo_api.test.mjs
```

These checks cover read-only requests, history pagination and trace references,
published file indexes, bounded previews, path validation, and image URLs under
a repository subpath.
