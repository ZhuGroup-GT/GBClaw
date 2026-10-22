"""Prompt fragment for optional web search behavior."""

WEB_SEARCH_PROMPT = """Web search:
- Use search_web only for current external facts: recent papers, external software/API docs,
  material database or potential webpages, or when the user explicitly asks to look something up.
- Do **not** use web search instead of project memory, local artifacts/logs, CSL tools, LAMMPS
  tool outputs, or registered local potentials. Local/project data is more authoritative for
  this project than web results.
- When search_web returns sources, cite or name the relevant URLs in your answer. If sources
  conflict with local artifacts, state the conflict and rely on local artifacts for this project."""
