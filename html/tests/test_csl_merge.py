"""Read-only integrity checks for the CSL background prepended to DemoProj.

These tests use only DemoProj, so the source project need not be published.
"""

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import unittest


PROJECT = Path(__file__).resolve().parents[2] / "projects" / "DemoProj"
THREAD_ID = "273561be-b61d-496f-b03f-78d42f577e1a"
IMPORTED_TURNS = (
    "7056affc-a772-47c1-9ac4-b1ba3e23d8b5",
    "8c32b44d-a064-4b61-86c0-98e545d66f2a",
    "b195e370-81c2-4482-815a-a92e359c946d",
    "02bbc733-ff1f-46a5-865c-fac714de6b46",
)
RUN_TURNS = {
    "csl-20260807-215816-a5c75e": IMPORTED_TURNS[1],
    "csl-20260807-215836-b2bd26": IMPORTED_TURNS[1],
    "csl-20260808-035152-67c8b7": IMPORTED_TURNS[3],
}
STALE_REFERENCES = (
    "proj-20260807-9c352c",
    "8daf80db-43f4-44fc-8161-e987eb4715ad",
    "39de23af-4535-4a73-9b45-920ac3e27584",
)
# Captured from the original target before the merge: content, IDs, and roles;
# sequence numbers intentionally change when the four background turns precede it.
ORIGINAL_TURNS_DIGEST = "d3b63f26d7c340342410722157001683f75eb07e5b3ac3626598e8ccfea9497d"
ORIGINAL_RUN_SOURCES_DIGEST = "5f04dd634520b26591011fcee1cfa4302972a048820ec3f2a5a5d0e29d6e4ecb"


def digest_rows(rows):
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def open_snapshot(relative_path):
    path = (PROJECT / relative_path).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


class CslMergeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chat = open_snapshot("chat/history.sqlite")
        cls.memory = open_snapshot("memory/index.sqlite")

    @classmethod
    def tearDownClass(cls):
        cls.chat.close()
        cls.memory.close()

    def imported_rows(self, table, column="run_id"):
        placeholders = ",".join("?" for _ in RUN_TURNS)
        return self.memory.execute(
            f"SELECT * FROM {table} WHERE {column} IN ({placeholders}) ORDER BY {column}",
            tuple(RUN_TURNS),
        ).fetchall()

    def project_path(self, relative_path):
        self.assertIsInstance(relative_path, str)
        self.assertTrue(relative_path)
        self.assertFalse(PurePosixPath(relative_path).is_absolute(), relative_path)
        self.assertNotIn("\\", relative_path)
        self.assertFalse(any(part in ("", ".", "..") for part in relative_path.split("/")), relative_path)
        path = (PROJECT / relative_path).resolve(strict=True)
        self.assertTrue(path.is_relative_to(PROJECT.resolve()), relative_path)
        return path

    def test_question_pairs_precede_unchanged_original_conversation(self):
        turns = self.chat.execute("SELECT * FROM turns ORDER BY sequence").fetchall()
        self.assertEqual(len(turns), 28)
        self.assertEqual([turn["turn_id"] for turn in turns[:4]], list(IMPORTED_TURNS))
        self.assertEqual([turn["role"] for turn in turns[:4]], ["user", "assistant"] * 2)
        self.assertEqual([turn["sequence"] for turn in turns], list(range(28)))
        self.assertEqual({turn["thread_id"] for turn in turns}, {THREAD_ID})
        self.assertEqual(turns[4]["turn_id"], "4636984f-d48d-48ab-9fc8-cf9d0826a5ed")
        original = [{key: turn[key] for key in ("turn_id", "role", "content")} for turn in turns[4:]]
        self.assertEqual(digest_rows(original), ORIGINAL_TURNS_DIGEST)

    def test_imported_traces_preserve_parent_relationships_without_planning(self):
        placeholders = ",".join("?" for _ in IMPORTED_TURNS)
        events = self.chat.execute(
            f"SELECT * FROM trace_events WHERE turn_id IN ({placeholders}) ORDER BY turn_id, sequence",
            IMPORTED_TURNS,
        ).fetchall()
        self.assertEqual(len(events), 20)
        by_id = {event["event_id"]: event for event in events}
        self.assertEqual(len(by_id), 20)
        for event in events:
            with self.subTest(event_id=event["event_id"]):
                self.assertIn(event["turn_id"], (IMPORTED_TURNS[1], IMPORTED_TURNS[3]))
                self.assertIsNone(re.search(r"(?:^|_)plans?(?:_|$)", event["tool_name"] or "", re.I))
                if event["parent_event_id"]:
                    parent = by_id[event["parent_event_id"]]
                    self.assertEqual(parent["turn_id"], event["turn_id"])
                    self.assertLess(parent["sequence"], event["sequence"])
        self.assertEqual(self.chat.execute("SELECT COUNT(*) FROM trace_events").fetchone()[0], 409)
        self.assertEqual(self.chat.execute("SELECT COUNT(*) FROM trace_events WHERE event_type='tool_start'").fetchone()[0], 176)
        self.assertEqual(self.chat.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_three_imported_artifacts_match_their_files_and_manifests(self):
        artifacts = self.imported_rows("artifacts")
        self.assertEqual(len(artifacts), 3)
        self.assertEqual({artifact["run_id"] for artifact in artifacts}, set(RUN_TURNS))
        runs = {run["run_id"]: run for run in self.imported_rows("runs")}
        for artifact in artifacts:
            with self.subTest(run_id=artifact["run_id"]):
                path = (PROJECT / artifact["path"]).resolve(strict=True)
                self.assertTrue(path.is_relative_to(PROJECT.resolve()))
                self.assertTrue(path.is_file())
                self.assertEqual(path.stat().st_size, artifact["size_bytes"])
                metadata = json.loads(artifact["metadata_json"])
                if "sha256" in metadata:
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), metadata["sha256"])
                manifest_path = PROJECT / runs[artifact["run_id"]]["manifest_path"]
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(manifest["run_id"], artifact["run_id"])
                entries = [item for item in manifest["files"] if item["artifact_id"] == artifact["artifact_id"]]
                self.assertEqual(len(entries), 1)
                entry = entries[0]
                self.assertEqual((manifest_path.parent / entry["path"]).resolve(), path)
                self.assertEqual(entry["metadata"], metadata)
                for key in ("role", "media_type", "provenance_class", "size_bytes"):
                    self.assertEqual(entry[key], artifact[key])

    def test_fact_provenance_points_to_the_imported_answers(self):
        facts = self.imported_rows("facts", "source_run_id")
        self.assertEqual(len(facts), 3)
        self.assertEqual({fact["source_run_id"] for fact in facts}, set(RUN_TURNS))
        for fact in facts:
            with self.subTest(fact_id=fact["fact_id"]):
                self.assertEqual(fact["kind"], "scientific_conclusion")
                self.assertEqual(fact["source_turn_id"], RUN_TURNS[fact["source_run_id"]])
                turn = self.chat.execute("SELECT role, thread_id FROM turns WHERE turn_id=?", (fact["source_turn_id"],)).fetchone()
                self.assertIsNotNone(turn)
                self.assertEqual((turn["role"], turn["thread_id"]), ("assistant", THREAD_ID))
        self.assertEqual(self.memory.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_imported_run_context_and_search_documents_use_the_target_workspace(self):
        runs = self.imported_rows("runs")
        self.assertEqual(len(runs), 3)
        for run in runs:
            self.assertEqual(run["workflow"], "csl")
            context = json.loads(run["parameters_json"])["_gbclaw_context"]
            self.assertEqual(context["project_id"], "DemoProj")
            self.assertEqual(context["chat_thread_id"], THREAD_ID)
        expected_docs = [("run", run["run_id"]) for run in runs]
        expected_docs += [("artifact", item["artifact_id"]) for item in self.imported_rows("artifacts")]
        expected_docs += [("fact", item["fact_id"]) for item in self.imported_rows("facts", "source_run_id")]
        self.assertEqual(len(expected_docs), 9)
        for entity_type, entity_id in expected_docs:
            with self.subTest(entity_type=entity_type, entity_id=entity_id):
                docs = self.memory.execute(
                    "SELECT * FROM memory_search WHERE entity_type=? AND entity_id=?", (entity_type, entity_id)
                ).fetchall()
                self.assertEqual(len(docs), 1)
                serialized = json.dumps(dict(docs[0]), ensure_ascii=False)
                for stale in STALE_REFERENCES:
                    self.assertNotIn(stale, serialized)

    def test_all_registered_artifacts_have_matching_files_and_manifest_entries(self):
        manifests = {}
        for run in self.memory.execute("SELECT run_id, manifest_path FROM runs"):
            manifest_path = self.project_path(run["manifest_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["run_id"], run["run_id"])
            manifests[run["run_id"]] = (manifest_path.parent, manifest["files"])
        artifacts = self.memory.execute("SELECT * FROM artifacts").fetchall()
        self.assertEqual(len(artifacts), 618)
        for artifact in artifacts:
            with self.subTest(artifact_id=artifact["artifact_id"]):
                path = self.project_path(artifact["path"])
                self.assertTrue(path.is_file())
                self.assertEqual(path.stat().st_size, artifact["size_bytes"])
                base, files = manifests[artifact["run_id"]]
                entries = [entry for entry in files if entry.get("artifact_id") == artifact["artifact_id"]]
                self.assertEqual(len(entries), 1)
                entry = entries[0]
                entry_path = (base / entry["path"]).resolve(strict=True)
                self.assertEqual(entry_path, path)
                self.assertEqual(entry["size_bytes"], artifact["size_bytes"])

    def test_all_structured_workspace_and_artifact_references_are_portable(self):
        def check(value, location):
            if isinstance(value, dict):
                for key, child in value.items():
                    child_location = f"{location}.{key}"
                    with self.subTest(location=child_location):
                        if key == "project_id":
                            self.assertEqual(child, "DemoProj")
                        if key == "path" and isinstance(child, str) and (
                            child.startswith("artifacts/") or "/artifacts/" in child
                        ):
                            self.assertTrue(child.startswith("artifacts/"), child)
                            self.project_path(child)
                    check(child, child_location)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    check(child, f"{location}[{index}]")

        for table, identity, columns in (
            ("runs", "run_id", ("parameters_json", "progress_json", "result_json")),
            ("facts", "fact_id", ("value_json",)),
        ):
            for row in self.memory.execute(f"SELECT * FROM {table}"):
                for column in columns:
                    check(json.loads(row[column]), f"{table}.{row[identity]}.{column}")

    def test_fact_turn_provenance_and_search_index_have_no_orphans(self):
        turn_ids = {row[0] for row in self.chat.execute("SELECT turn_id FROM turns")}
        for fact in self.memory.execute("SELECT fact_id, source_turn_id FROM facts"):
            with self.subTest(fact_id=fact["fact_id"]):
                if fact["source_turn_id"] is not None:
                    self.assertIn(fact["source_turn_id"], turn_ids)
        expected = set()
        for entity_type, table, identity, condition in (
            ("run", "runs", "run_id", ""),
            ("artifact", "artifacts", "artifact_id", ""),
            ("fact", "facts", "fact_id", " WHERE status='active'"),
        ):
            expected.update((entity_type, row[0]) for row in self.memory.execute(f"SELECT {identity} FROM {table}{condition}"))
        indexed = [tuple(row) for row in self.memory.execute("SELECT entity_type, entity_id FROM memory_search")]
        self.assertEqual(len(indexed), 789)
        self.assertEqual(len(indexed), len(set(indexed)), "Each active entity must be indexed exactly once.")
        self.assertEqual(set(indexed), expected, "Missing or stale FTS entries must be rebuilt with their entities.")

    def test_snapshot_databases_have_structural_and_foreign_key_integrity(self):
        for name, connection in (("chat", self.chat), ("memory", self.memory)):
            with self.subTest(database=name):
                self.assertEqual([tuple(row) for row in connection.execute("PRAGMA integrity_check")], [("ok",)])
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_memory_summary_and_existing_dependencies_remain_consistent(self):
        counts = {table: self.memory.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("runs", "artifacts", "facts")}
        self.assertEqual(counts, {"runs": 45, "artifacts": 618, "facts": 126})
        self.assertEqual(self.memory.execute("SELECT version FROM memory_schema").fetchone()[0], 4)
        sources = [dict(row) for row in self.memory.execute("SELECT * FROM run_sources ORDER BY run_id, relation, ordinal")]
        self.assertEqual(len(sources), 155)
        self.assertEqual(digest_rows(sources), ORIGINAL_RUN_SOURCES_DIGEST)
        summary = self.memory.execute("SELECT * FROM semantic_summary WHERE singleton_id=1").fetchone()
        text = (PROJECT / "memory/summary.md").read_text(encoding="utf-8")
        self.assertEqual(summary["content"], text.strip())
        self.assertIn("P012", text)
        self.assertLess(
            text.index("## CSL and near-CSL screening context"),
            text.index("## Crystallography and P012 geometry"),
        )
        for run_id in RUN_TURNS:
            self.assertIn(run_id, text)
        self.assertEqual(summary["format_version"], 3)
        self.assertEqual((summary["run_count"], summary["fact_count"]), (45, 126))
        revision = self.memory.execute("SELECT current_revision FROM semantic_revision").fetchone()[0]
        self.assertGreaterEqual(revision, 585)
        self.assertEqual(summary["source_revision"], revision)
        summary_source = {"format_version": summary["format_version"], "source_revision": revision}
        self.assertEqual(summary["source_digest"], "sha256:" + digest_rows(summary_source))


if __name__ == "__main__":
    unittest.main()
