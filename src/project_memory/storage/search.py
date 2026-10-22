"""FTS5 trigram search over runs, facts and artifacts."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Iterable

from extensions import SEARCH_ALIASES
from project_memory.models import (
    FactSourceType,
    FactStatus,
    MemorySearchPage,
    MemorySearchResult,
)
from project_memory.storage.facts import _fact_source_sql
from project_memory.storage.runs import RunStoreMixin
from project_workspace.persistence.sqlite import connect_project_db, transaction

_SEARCH_INDEX_VERSION = 5


_SEARCH_ENTITY_TYPES = ("fact", "run", "artifact")


_SEARCH_TERMS_RE = re.compile(r"\w+", re.UNICODE)


_MAX_SEARCH_QUERY_CHARS = 500


_MAX_SEARCH_TERMS = 24


_MAX_SEARCH_SNIPPET_CHARS = 800


_SEARCH_ALIASES = SEARCH_ALIASES


def _search_terms(query: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    candidates = [match.group(0) for match in _SEARCH_TERMS_RE.finditer(query)]
    aliases = [value for phrase, value in _SEARCH_ALIASES.items() if phrase in query]
    if aliases:
        candidates = aliases + re.findall(r"[A-Za-z][A-Za-z0-9_]*|\d+", query) + candidates
    for term in candidates:
        normalized = term.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        terms.append(term)
        if len(terms) >= _MAX_SEARCH_TERMS:
            break
    return terms


def _fts_match_expression(terms: list[str]) -> str | None:
    # The trigram tokenizer cannot match terms shorter than three Unicode
    # characters. Use weighted matching for Fe, Σ5, and similar short IDs.
    if not terms or any(len(term) < 3 for term in terms):
        return None
    quoted = [term.replace('"', '""') for term in terms]
    return " OR ".join(f'"{term}"' for term in quoted)


def _bounded_search_text(value: str) -> str:
    text = value.strip()
    if len(text) <= _MAX_SEARCH_SNIPPET_CHARS:
        return text
    return text[: _MAX_SEARCH_SNIPPET_CHARS - 1].rstrip() + "…"


class SearchIndexMixin:
    def search_all(
        self,
        query: str,
        *,
        entity_types: list[str] | tuple[str, ...] | None = None,
        limit: int = 10,
        offset: int = 0,
        completed_only: bool = False,
        excluded_fact_kinds: Iterable[str] = (),
        source_type: FactSourceType | str | None = None,
    ) -> MemorySearchPage:
        """Search memory; a source_type filter selects only fact records."""
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")
        if len(normalized_query) > _MAX_SEARCH_QUERY_CHARS:
            raise ValueError(
                f"query must be at most {_MAX_SEARCH_QUERY_CHARS} characters"
            )
        if limit < 1:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")

        requested_types = list(entity_types or (("fact",) if source_type is not None else _SEARCH_ENTITY_TYPES))
        normalized_types: list[str] = []
        for entity_type in requested_types:
            normalized = str(entity_type).strip().lower()
            if normalized not in _SEARCH_ENTITY_TYPES:
                allowed = ", ".join(_SEARCH_ENTITY_TYPES)
                raise ValueError(
                    f"Unknown entity type '{entity_type}'. Allowed values: {allowed}"
                )
            if normalized not in normalized_types:
                normalized_types.append(normalized)
        if not normalized_types:
            raise ValueError("entity_types must contain at least one value")
        source_values = ()
        source_clause = ""
        if source_type is not None:
            source_values = (FactSourceType(source_type).value,)
            if any(entity_type != "fact" for entity_type in normalized_types):
                raise ValueError("source_type filters apply only to facts")
            source_clause = f"""
                AND EXISTS (
                    SELECT 1 FROM facts f WHERE f.fact_id = entity_id
                    AND ({_fact_source_sql('f.')}) = ?
                )
            """

        terms = _search_terms(normalized_query)
        match_expression = _fts_match_expression(terms)
        placeholders = ",".join("?" for _ in normalized_types)
        fetch_limit = limit + 1
        # Filter before LIMIT/OFFSET: a page of failed runs must not hide an
        # older completed result. Explicit user choices remain valid independently.
        completed_clause = ""
        if completed_only:
            completed_clause = """
                AND (
                    (entity_type = 'run' AND EXISTS (
                        SELECT 1 FROM runs r WHERE r.run_id = entity_id
                        AND r.status = 'completed'
                    ))
                    OR (entity_type = 'artifact' AND EXISTS (
                        SELECT 1 FROM artifacts a JOIN runs r ON r.run_id = a.run_id
                        WHERE a.artifact_id = entity_id AND r.status = 'completed'
                    ))
                    OR (entity_type = 'fact' AND EXISTS (
                        SELECT 1 FROM facts f LEFT JOIN runs r ON r.run_id = f.source_run_id
                        WHERE f.fact_id = entity_id AND f.status = 'active'
                        AND (f.evidence_kind = 'user_explicit'
                             OR f.source_run_id IS NULL OR r.status = 'completed')
                    ))
                )
            """
        excluded_kinds = tuple(dict.fromkeys(excluded_fact_kinds))
        excluded_clause = ""
        if excluded_kinds:
            excluded_clause = f"""
                AND NOT (entity_type = 'fact' AND EXISTS (
                    SELECT 1 FROM facts f WHERE f.fact_id = entity_id
                    AND f.kind IN ({','.join('?' for _ in excluded_kinds)})
                ))
            """
        conn = connect_project_db(self.db_path)
        try:
            if match_expression is not None:
                rows = conn.execute(
                    f"""
                    SELECT entity_type, entity_id, title,
                           snippet(memory_search, -1, '[', ']', ' … ', 48) AS snippet,
                           bm25(memory_search, 0.0, 0.0, 5.0, 1.0, 0.0) AS rank,
                           updated_at
                    FROM memory_search
                    WHERE memory_search MATCH ?
                      AND entity_type IN ({placeholders})
                      {completed_clause}
                      {excluded_clause}
                      {source_clause}
                    ORDER BY rank, updated_at DESC, entity_type, entity_id
                    LIMIT ? OFFSET ?
                    """,
                    (
                        match_expression,
                        *normalized_types,
                        *excluded_kinds,
                        *source_values,
                        fetch_limit,
                        offset,
                    ),
                ).fetchall()
            else:
                if not terms:
                    terms = [normalized_query]
                # Plain substring Fe also matches "manifest", a field in
                # practically every run. Match short scientific symbols/indices
                # as tokens; underscores and adjacent Chinese text count as
                # separators. Chinese short words still match unsegmented text.
                short_patterns = {
                    term: re.compile(
                        re.escape(term) if any("\u3400" <= ch <= "\u9fff" for ch in term)
                        else r"(?<![^\W_\u3400-\u9fff])" + re.escape(term) + r"(?![^\W_\u3400-\u9fff])",
                        re.IGNORECASE,
                    )
                    for term in terms if len(term) < 3
                }
                conn.create_function(
                    "memory_short_match", 2,
                    lambda term, value: int(short_patterns[term].search(value or "") is not None),
                    deterministic=True,
                )
                like_clauses: list[str] = []
                like_values: list[str] = []
                score_clauses: list[str] = []
                score_values: list[str] = []
                for term in terms:
                    escaped = (
                        term.replace("\\", "\\\\")
                        .replace("%", "\\%")
                        .replace("_", "\\_")
                    )
                    if len(term) < 3:
                        pattern = term
                        title_match = "memory_short_match(?, title)"
                        body_match = "memory_short_match(?, body)"
                    else:
                        pattern = f"%{escaped}%"
                        title_match = "title LIKE ? ESCAPE '\\'"
                        body_match = "body LIKE ? ESCAPE '\\'"
                    like_clauses.append(f"({title_match} OR {body_match})")
                    like_values.extend((pattern, pattern))
                    # Short terms (Fe, Ni, axis indices) must not turn the
                    # entire query into a recency-only search. Rank coverage
                    # of meaningful longer terms first, then short matches.
                    weight = 4 if len(term) >= 3 else 1
                    score_clauses.append(
                        f"(CASE WHEN {title_match} THEN {5 * weight} "
                        f"WHEN {body_match} THEN {weight} ELSE 0 END)"
                    )
                    score_values.extend((pattern, pattern))
                rows = conn.execute(
                    f"""
                    SELECT entity_type, entity_id, title,
                           substr(body, 1, ?) AS snippet,
                           -({' + '.join(score_clauses)}) AS rank,
                           updated_at
                    FROM memory_search
                    WHERE entity_type IN ({placeholders})
                      AND ({' OR '.join(like_clauses)})
                      {completed_clause}
                      {excluded_clause}
                      {source_clause}
                    ORDER BY rank, updated_at DESC, entity_type, entity_id
                    LIMIT ? OFFSET ?
                    """,
                    (
                        _MAX_SEARCH_SNIPPET_CHARS,
                        *score_values,
                        *normalized_types,
                        *like_values,
                        *excluded_kinds,
                        *source_values,
                        fetch_limit,
                        offset,
                    ),
                ).fetchall()

            has_more = len(rows) > limit
            results: list[MemorySearchResult] = []
            for row in rows[:limit]:
                entity_type = str(row["entity_type"])
                entity_id = str(row["entity_id"])
                results.append(
                    MemorySearchResult(
                        entity_type=entity_type,
                        entity_id=entity_id,
                        title=_bounded_search_text(str(row["title"])),
                        snippet=_bounded_search_text(str(row["snippet"] or "")),
                        rank=float(row["rank"] or 0.0),
                        updated_at=str(row["updated_at"]),
                        reference=self._search_reference(
                            conn, entity_type=entity_type, entity_id=entity_id
                        ),
                    )
                )
            return MemorySearchPage(
                query=normalized_query,
                entity_types=normalized_types,
                offset=offset,
                limit=limit,
                has_more=has_more,
                next_offset=offset + limit if has_more else None,
                results=results,
            )
        finally:
            conn.close()

    def rebuild_search_index(self) -> None:
        """Rebuild the derived search index from authoritative source tables."""
        conn = connect_project_db(self.db_path)
        try:
            with transaction(conn):
                self._rebuild_search_index(conn, force=True)
        finally:
            conn.close()

    @classmethod
    def _rebuild_search_index(
        cls,
        conn: sqlite3.Connection,
        *,
        force: bool = False,
    ) -> None:
        current_version = int(
            conn.execute(
                "SELECT index_version FROM memory_search_state WHERE singleton_id = 1"
            ).fetchone()[0]
        )
        if current_version == _SEARCH_INDEX_VERSION and not force:
            return
        conn.execute("DELETE FROM memory_search")
        for row in conn.execute("SELECT run_id FROM runs ORDER BY run_id"):
            cls._upsert_run_search_document(conn, str(row["run_id"]))
        for row in conn.execute("SELECT fact_id FROM facts ORDER BY fact_id"):
            cls._upsert_fact_search_document(conn, str(row["fact_id"]))
        for row in conn.execute("SELECT artifact_id FROM artifacts ORDER BY artifact_id"):
            cls._upsert_artifact_search_document(conn, str(row["artifact_id"]))
        conn.execute(
            "UPDATE memory_search_state SET index_version = ? WHERE singleton_id = 1",
            (_SEARCH_INDEX_VERSION,),
        )

    @staticmethod
    def _delete_search_document(
        conn: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
    ) -> None:
        conn.execute(
            "DELETE FROM memory_search WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        )

    @classmethod
    def _upsert_run_search_document(
        cls,
        conn: sqlite3.Connection,
        run_id: str,
    ) -> None:
        row = conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            cls._delete_search_document(conn, "run", run_id)
            return
        sources = cls._run_sources_from_rows(
            conn.execute(
                "SELECT * FROM run_sources WHERE run_id = ? "
                "ORDER BY relation, ordinal, source_run_id",
                (run_id,),
            ).fetchall()
        )
        title = f"run {row['run_id']} {row['workflow']} {row['status']}"
        body = "\n".join(
            (
                f"run_id: {row['run_id']}",
                f"workflow: {row['workflow']}",
                f"status: {row['status']}",
                f"source_run_id: {row['source_run_id'] or ''}",
                f"source_fingerprint: {row['source_fingerprint'] or ''}",
                "sources: "
                + json.dumps(
                    [item.model_dump(mode="json") for item in sources],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                f"config_path: {row['config_path'] or ''}",
                f"manifest_path: {row['manifest_path']}",
                f"parameters: {row['parameters_json']}",
                f"progress: {row['progress_json']}",
                f"result: {row['result_json']}",
                f"error: {row['error']}",
            )
        )
        cls._replace_search_document(
            conn,
            entity_type="run",
            entity_id=run_id,
            title=title,
            body=body,
            updated_at=str(row["updated_at"]),
        )

    @classmethod
    def _upsert_fact_search_document(
        cls,
        conn: sqlite3.Connection,
        fact_id: str,
    ) -> None:
        row = conn.execute(
            "SELECT * FROM facts WHERE fact_id = ?", (fact_id,)
        ).fetchone()
        if row is None or row["status"] != FactStatus.ACTIVE.value:
            cls._delete_search_document(conn, "fact", fact_id)
            return
        title = f"fact {row['kind']} {row['lookup_key']}"
        body = "\n".join(
            (
                f"fact_id: {row['fact_id']}",
                f"kind: {row['kind']}",
                f"lookup_key: {row['lookup_key']}",
                f"version: {row['version']}",
                f"value: {row['value_json']}",
                f"source_turn_id: {row['source_turn_id'] or ''}",
                f"source_run_id: {row['source_run_id'] or ''}",
                f"evidence_kind: {row['evidence_kind']}",
            )
        )
        cls._replace_search_document(
            conn,
            entity_type="fact",
            entity_id=fact_id,
            title=title,
            body=body,
            updated_at=str(row["updated_at"]),
        )

    @classmethod
    def _upsert_artifact_search_document(
        cls,
        conn: sqlite3.Connection,
        artifact_id: str,
    ) -> None:
        row = conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        if row is None:
            cls._delete_search_document(conn, "artifact", artifact_id)
            return
        title = f"artifact {row['role']} {row['path']}"
        body = "\n".join(
            (
                f"artifact_id: {row['artifact_id']}",
                f"run_id: {row['run_id']}",
                f"role: {row['role']}",
                f"path: {row['path']}",
                f"provenance_class: {row['provenance_class']}",
                f"media_type: {row['media_type'] or ''}",
                "size_bytes: "
                + (str(row["size_bytes"]) if row["size_bytes"] is not None else ""),
                f"metadata: {row['metadata_json']}",
            )
        )
        cls._replace_search_document(
            conn,
            entity_type="artifact",
            entity_id=artifact_id,
            title=title,
            body=body,
            updated_at=str(row["created_at"]),
        )

    @classmethod
    def _replace_search_document(
        cls,
        conn: sqlite3.Connection,
        *,
        entity_type: str,
        entity_id: str,
        title: str,
        body: str,
        updated_at: str,
    ) -> None:
        cls._delete_search_document(conn, entity_type, entity_id)
        conn.execute(
            "INSERT INTO memory_search "
            "(entity_type, entity_id, title, body, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (entity_type, entity_id, title, body, updated_at),
        )

    @staticmethod
    def _search_reference(
        conn: sqlite3.Connection,
        *,
        entity_type: str,
        entity_id: str,
    ) -> dict[str, Any]:
        if entity_type == "fact":
            row = conn.execute(
                "SELECT fact_id, kind, lookup_key, status, version, "
                "source_turn_id, source_run_id, evidence_kind, "
                f"{_fact_source_sql()} AS source_type FROM facts "
                "WHERE fact_id = ? AND status = ?",
                (entity_id, FactStatus.ACTIVE.value),
            ).fetchone()
            if row is not None:
                return {
                    "fact_id": str(row["fact_id"]),
                    "fact_kind": str(row["kind"]),
                    "lookup_key": str(row["lookup_key"]),
                    "status": str(row["status"]),
                    "version": int(row["version"]),
                    "source_turn_id": row["source_turn_id"],
                    "source_run_id": row["source_run_id"],
                    "evidence_kind": str(row["evidence_kind"]),
                    "source_type": str(row["source_type"]),
                }
        elif entity_type == "run":
            row = conn.execute(
                "SELECT run_id, workflow, status, source_run_id "
                "FROM runs WHERE run_id = ?",
                (entity_id,),
            ).fetchone()
            if row is not None:
                sources = RunStoreMixin._run_sources_from_rows(
                    conn.execute(
                        "SELECT * FROM run_sources WHERE run_id = ? "
                        "ORDER BY relation, ordinal, source_run_id",
                        (entity_id,),
                    ).fetchall()
                )
                return {
                    "run_id": str(row["run_id"]),
                    "workflow": str(row["workflow"]),
                    "status": str(row["status"]),
                    "source_run_id": row["source_run_id"],
                    "sources": [
                        item.model_dump(mode="json") for item in sources
                    ],
                }
        elif entity_type == "artifact":
            row = conn.execute(
                "SELECT artifact_id, run_id, role, path, media_type, provenance_class "
                "FROM artifacts WHERE artifact_id = ?",
                (entity_id,),
            ).fetchone()
            if row is not None:
                return {
                    "artifact_id": str(row["artifact_id"]),
                    "run_id": str(row["run_id"]),
                    "role": str(row["role"]),
                    "path": str(row["path"]),
                    "media_type": row["media_type"],
                    "provenance_class": str(row["provenance_class"]),
                }
        return {"entity_id": entity_id}
