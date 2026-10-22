"""Project working summaries, compaction, durable archives and retrieval views.

Session summaries live in agents.middleware; read-only project context in
project_memory.snapshot. Import service only when a refresh is requested.
"""
