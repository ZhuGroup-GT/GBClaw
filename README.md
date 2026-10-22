# GBClaw research showcase

This branch contains the public, read-only GBClaw research workspace
`projects/Demo-GPT`. It follows an FCC Ni
`(110) ∥ (111)` tilt boundary study through cell selection, minimization, shear
loading, unloading and reloading, transition-path analysis, and paired-grain
displacement analysis through its recorded
conversation, files, figures, and memory.

The website includes the recorded conversation, execution traces loaded on
expansion, project memory, a file browser with previews and downloads, recorded
figures, and an interactive view of the saved dichromatic map. Map controls explore
saved coordinates; they do not compute new structures. Live chat, project editing,
and new calculations are unavailable in this demonstration.

## Model configuration

The entire recorded Demo-GPT study used **GPT-6.1 Sol** for every agent:

| Model | Agents |
| --- | --- |
| GPT-6.1 Sol | Orchestrator + Coding + Structure + LAMMPS + Analysis |

## Recorded files

To keep this showcase lightweight, some large files have been removed from the
project. Intermediate NEB and minimization trajectories, per-item initial CFG
conversions, and runtime checkpoint databases are omitted. Each batch retains
all 18 final structures in both CFG and LAMMPS data formats, with its metrics and
energy records. Duplicate per-item finals and staged inputs use the retained
canonical structures; duplicate potentials use one shared copy.
Recorded conversations, downloadable transcripts, figures, and analysis tables
are retained. Run manifests record omissions and retained replacements, including
the additional removals in `pruned_from_demo`.

## Preview locally

From the repository root:

```sh
python3 -m http.server 8000 --bind 127.0.0.1
```

Open `http://localhost:8000/` for Demo-GPT. Serve the
repository root so that both `html/` and `projects/` are accessible. The website uses browser modules and HTTP
requests, so opening `index.html` directly as a local file is insufficient.

## Static hosting

Publish the repository root, preserving the relative paths of `html/` and
`projects/`. The root entry point redirects to `html/`, preserving the project
query parameter; all data and file
URLs work under a repository subpath. `.nojekyll` allows ordinary static serving
without transforming the recorded workspace.

No research backend or API credentials are required. The private development
repository is not included. This implementation has not been deployed by the
local development changes.

## Refresh the recorded data

After updating the project, regenerate its browsing snapshot:

```sh
python3 html/scripts/build_demo.py
```

The standard-library script reads the existing SQLite databases in immutable,
read-only mode and generates `html/data/projects.json` and the Demo-GPT
snapshot in `html/data/`, including `demo.json`, `history.json`, and lazy turn
details. It indexes original project files without copying them. Caches, hidden
entries, lock files, and SQLite sidecars are omitted from the browser index.
If a project has no `chat/transcript.md`, the script exports its complete messages
and untruncated traces from `chat/history.sqlite`. Existing transcripts and the
SQLite databases remain unchanged.
Tool traces use the default workspace previews: ordinary inputs are limited to
280 characters and outputs to 400, including the truncation ellipsis. Delegated
tasks show up to 1,200 characters; their result summaries show up to 8,000.
Image references are extracted before truncation, so recorded figures remain
available. Original transcripts and numerical artifacts remain downloadable.
Text file previews are limited to the first 256 KB, with full-file downloads
available alongside them.

## Validation

```sh
node html/tests/demo_api.test.mjs
python3 -m unittest discover -s html/tests -p 'test_*.py'
```

These checks cover read-only requests, history pagination and trace references,
published file indexes, bounded previews, path validation, and image URLs under
a repository subpath.
