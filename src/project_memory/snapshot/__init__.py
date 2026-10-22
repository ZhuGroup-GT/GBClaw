"""Read-only project data snapshots for memory consumers.

This package returns project data without Agent runtime types. Thread dialogue
and Session summary compaction belong to agents.middleware. Imports are explicit
so reading a snapshot does not load the summary model or Agent middleware.
Project-summary writing, compaction and archives belong to project_memory.summary.
"""
