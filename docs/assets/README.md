# Project artwork and figure sources

## Logos and icons

This directory is the source of truth for the project artwork. The root README and documentation use the horizontal logo; the Web UI header and browser tab use the square icon. The API serves these same files at `/brand-assets/`, so updating the assets here also updates the Web UI without copying files into `web/`.

| Asset | Use |
| --- | --- |
| [GBClaw-logo.svg](GBClaw-logo.svg) | Horizontal logo with dark lettering, for light backgrounds. |
| [GBClaw-logo-light.svg](GBClaw-logo-light.svg) | Horizontal logo with light lettering, for dark backgrounds. |
| [GBClaw-icon.svg](GBClaw-icon.svg) | Square icon with dark outlines, for light backgrounds. |
| [GBClaw-icon-light.svg](GBClaw-icon-light.svg) | Square icon with light outlines, for dark backgrounds. |

The `-light` suffix refers to the artwork's foreground colors. Documentation uses a `<picture>` element to select a variant by color preference. The Web UI selects its icon using the active application theme. `/favicon.ico` serves the default SVG icon for clients that request that conventional path.

## Manuscript figures

These figures are extracted from the author-provided manuscript *GBClaw: An agentic AI framework for atomistic grain boundary research and mechanistic discovery*. See [References](../references.md) for the author list.

| Asset | Source in the framework manuscript | Original image dimensions |
| --- | --- | --- |
| [paper-architecture.png](paper-architecture.png) | Figure 1, PDF page 5 | 1484 × 985 |
| [paper-research-workflow.png](paper-research-workflow.png) | Figure 2, PDF page 7 | 1425 × 719 |

The embedded RGB images and their PDF soft masks were extracted with `pdfimages`, combined, and composited onto white to preserve their appearance on the manuscript page. Labels and diagram content are unchanged. These assets are self-contained and do not require the source PDF to render.

The manuscript diagrams describe the overall architecture; the README and implementation guides describe which integrations and runtimes are currently available. The architecture figure used by the documentation is `paper-architecture.png`.
