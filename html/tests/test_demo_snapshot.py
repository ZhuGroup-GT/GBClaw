"""Validate the complete published study after removing one duplicate exchange."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import unittest


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "projects" / "Demo-GPT"
THREAD_ID = "c1d2f74f-95b9-4aba-a105-126d2a65d6ac"
REMOVED_IDENTIFIERS = (
    "b1ccad5b-075b-4330-ba82-0a7de9c88725",
    "ff556757-67cb-48d8-ae0f-e3f1e91591a5",
    "f4350dac-ecc0-44c3-a48d-0228cbb56a39",
    "c910f6d9-94ee-4098-a46e-34c460a46f4c",
)
# Captured from the supplied study before cleanup, with only the duplicate
# exchange omitted and the public project name normalized.
SURVIVING_MESSAGES_SHA = "90f32c4a5eb407fac2ddce81a6c69e8047915c77c1984431cff15b2467143691"
TRACE_RELATIONSHIPS_SHA = "fa14ab1eb5af75b75e721a3f3caf65c0c380fd974e58776fee9353c6d6d9d486"
RUN_RELATIONSHIPS_SHA = "98c15ab1d88402a3d6a5372398df7a0134aacf38e2d61b99601871dffbb9f69f"


def digest_rows(rows):
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def open_snapshot(relative_path):
    connection = sqlite3.connect(
        (PROJECT / relative_path).resolve(strict=True).as_uri() + "?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    return connection


class DemoSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chat = open_snapshot("chat/history.sqlite")
        cls.memory = open_snapshot("memory/index.sqlite")

    @classmethod
    def tearDownClass(cls):
        cls.chat.close()
        cls.memory.close()

    def project_path(self, relative_path):
        self.assertIsInstance(relative_path, str)
        self.assertTrue(relative_path)
        self.assertFalse(PurePosixPath(relative_path).is_absolute(), relative_path)
        self.assertNotIn("\\", relative_path)
        self.assertFalse(any(part in ("", ".", "..") for part in relative_path.split("/")), relative_path)
        path = (PROJECT / relative_path).resolve(strict=True)
        self.assertTrue(path.is_relative_to(PROJECT.resolve()), relative_path)
        return path

    def test_surviving_messages_are_complete_and_renumbered_without_gaps(self):
        turns = self.chat.execute("SELECT * FROM turns ORDER BY sequence").fetchall()
        self.assertEqual(len(turns), 30)
        self.assertEqual([turn["sequence"] for turn in turns], list(range(30)))
        self.assertEqual([turn["role"] for turn in turns], ["user", "assistant"] * 15)
        self.assertEqual({turn["thread_id"] for turn in turns}, {THREAD_ID})
        messages = [{key: turn[key] for key in ("turn_id", "role", "content")} for turn in turns]
        self.assertEqual(digest_rows(messages), SURVIVING_MESSAGES_SHA)
        self.assertEqual(turns[24]["turn_id"], "d2d92b5a-6db8-4936-b570-f0e8168b9119")
        self.assertEqual(turns[26]["content"], "Apply strain to my saved selection")
        self.assertIn("Strain was successfully applied", turns[27]["content"])
        self.assertIn("Project summary", turns[29]["content"])
        order = self.chat.execute("SELECT * FROM turn_order ORDER BY position").fetchall()
        self.assertEqual([row["position"] for row in order], list(range(1, 31)))
        self.assertEqual([row["turn_id"] for row in order], [turn["turn_id"] for turn in turns])
        self.assertEqual(self.chat.execute("SELECT seq FROM sqlite_sequence WHERE name='turn_order'").fetchone()[0], 30)

    def test_original_tool_traces_preserve_their_ids_order_and_parent_relationships(self):
        events = self.chat.execute("SELECT * FROM trace_events ORDER BY turn_id, sequence").fetchall()
        self.assertEqual(len(events), 443)
        columns = ("event_id", "turn_id", "parent_event_id", "tool_call_id", "sequence", "event_type", "tool_name")
        self.assertEqual(digest_rows([{key: event[key] for key in columns} for event in events]), TRACE_RELATIONSHIPS_SHA)
        by_id = {event["event_id"]: event for event in events}
        self.assertEqual(len(by_id), len(events))
        for event in events:
            if event["parent_event_id"]:
                parent = by_id[event["parent_event_id"]]
                self.assertEqual(parent["turn_id"], event["turn_id"])
                self.assertLess(parent["sequence"], event["sequence"])
            for field in ("input_json", "output_text"):
                for output_id in re.findall(r'"output_id"\s*:\s*"([0-9a-f]{64})"', event[field] or ""):
                    path = self.project_path(f"runtime/tool_outputs/{output_id}.json")
                    self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)
        self.assertEqual(sum(event["event_type"] == "tool_start" for event in events), 193)

    def test_requests_and_retry_jobs_reference_existing_assistant_turns(self):
        requests = self.chat.execute("SELECT * FROM chat_requests").fetchall()
        self.assertEqual(len(requests), 15)
        for request in requests:
            turn = self.chat.execute("SELECT * FROM turns WHERE turn_id=?", (request["assistant_turn_id"],)).fetchone()
            self.assertIsNotNone(turn)
            self.assertEqual(turn["role"], "assistant")
            self.assertEqual(request["thread_id"], THREAD_ID)
            response = json.loads(request["response_json"])
            self.assertEqual(response["reply"], turn["content"])
            self.assertEqual(response["request_id"], request["request_id"])
            self.assertEqual(response["project_id"], "Demo-GPT")
            self.assertEqual(response["thread_id"], THREAD_ID)
        for job in self.memory.execute("SELECT * FROM memory_retry_jobs"):
            self.assertIsNotNone(self.chat.execute(
                "SELECT 1 FROM turns WHERE turn_id=? AND role='assistant'", (job["assistant_turn_id"],)
            ).fetchone())

    def test_deleted_exchange_has_no_residual_database_or_export_bytes(self):
        # Raw SQLite bytes also catch deleted rows left in freelist pages.
        public_paths = [path for base in (PROJECT, ROOT / "html/data") for path in base.rglob("*") if path.is_file()]
        for path in public_paths:
            if path.suffix.lower() not in {".sqlite", ".json", ".md", ".txt"}:
                continue
            value = path.read_bytes()
            for identifier in REMOVED_IDENTIFIERS:
                self.assertNotIn(identifier.encode(), value, str(path))
        transcript = (PROJECT / "chat/transcript.md").read_text(encoding="utf-8")
        headers = re.findall(r"^## (\d{4}) · (User|Assistant)$", transcript, re.MULTILINE)
        self.assertEqual(headers, [(f"{number:04d}", "User" if number % 2 == 0 else "Assistant") for number in range(30)])
        self.assertEqual(transcript.count("I confirmed the near-CSL unit cell in the DichromaticMap widget."), 1)
        self.assertNotIn("Your message contains conflicting instructions:", transcript)
        for turn in self.chat.execute("SELECT turn_id, content FROM turns"):
            self.assertIn(turn["content"].strip(), transcript, turn["turn_id"])
        export = self.chat.execute("SELECT * FROM transcript_export").fetchone()
        self.assertEqual(export["revision"], export["exported_revision"])

    def test_registered_artifacts_match_files_and_manifests(self):
        manifests = {}
        for run in self.memory.execute("SELECT run_id, manifest_path FROM runs"):
            manifest_path = self.project_path(run["manifest_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["run_id"], run["run_id"])
            manifests[run["run_id"]] = (manifest_path.parent, manifest["files"])
        for artifact in self.memory.execute("SELECT * FROM artifacts"):
            with self.subTest(artifact_id=artifact["artifact_id"]):
                path = self.project_path(artifact["path"])
                self.assertTrue(path.is_file())
                self.assertEqual(path.stat().st_size, artifact["size_bytes"])
                metadata = json.loads(artifact["metadata_json"])
                if "sha256" in metadata:
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), metadata["sha256"])
                base, files = manifests[artifact["run_id"]]
                entries = [entry for entry in files if entry.get("artifact_id") == artifact["artifact_id"]]
                self.assertEqual(len(entries), 1)
                entry = entries[0]
                self.assertEqual((base / entry["path"]).resolve(strict=True), path)
                for key in ("size_bytes", "role", "media_type", "provenance_class"):
                    self.assertEqual(entry[key], artifact[key])
                self.assertEqual(entry.get("metadata", {}), metadata)

    def test_fact_provenance_and_search_entities_have_no_orphans(self):
        turn_ids = {row[0] for row in self.chat.execute("SELECT turn_id FROM turns")}
        for fact in self.memory.execute("SELECT fact_id, source_turn_id FROM facts"):
            if fact["source_turn_id"] is not None:
                self.assertIn(fact["source_turn_id"], turn_ids, fact["fact_id"])
        expected = set()
        for entity_type, table, identity, condition in (
            ("run", "runs", "run_id", ""),
            ("artifact", "artifacts", "artifact_id", ""),
            ("fact", "facts", "fact_id", " WHERE status='active'"),
        ):
            expected.update((entity_type, row[0]) for row in self.memory.execute(f"SELECT {identity} FROM {table}{condition}"))
        indexed = [tuple(row) for row in self.memory.execute("SELECT entity_type, entity_id FROM memory_search")]
        self.assertEqual(len(indexed), len(set(indexed)))
        self.assertEqual(set(indexed), expected)

    def test_scientific_run_lineage_is_unchanged(self):
        self.assertEqual(self.memory.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 29)
        self.assertEqual(self.memory.execute("SELECT COUNT(*) FROM facts").fetchone()[0], 118)
        columns = ("run_id", "source_run_id", "relation", "ordinal")
        sources = self.memory.execute("SELECT * FROM run_sources ORDER BY run_id, relation, ordinal").fetchall()
        self.assertEqual(len(sources), 110)
        self.assertEqual(digest_rows([{key: row[key] for key in columns} for row in sources]), RUN_RELATIONSHIPS_SHA)

    def test_summary_database_markdown_and_revision_agree(self):
        summary = self.memory.execute("SELECT * FROM semantic_summary WHERE singleton_id=1").fetchone()
        self.assertEqual(summary["content"], (PROJECT / "memory/summary.md").read_text(encoding="utf-8").strip())
        self.assertEqual(summary["format_version"], 4)
        self.assertEqual((summary["run_count"], summary["fact_count"]), (29, 118))
        revision = self.memory.execute("SELECT current_revision FROM semantic_revision").fetchone()[0]
        self.assertEqual(summary["source_revision"], revision)
        summary_source = {"format_version": summary["format_version"], "source_revision": revision}
        self.assertEqual(summary["source_digest"], "sha256:" + digest_rows(summary_source))
        for archive in self.memory.execute("SELECT archive_id, content FROM semantic_summary_archives"):
            path = self.project_path(f"memory/summary_archive/{archive['archive_id']}.md")
            self.assertTrue(path.read_text(encoding="utf-8").strip().endswith(archive["content"].strip()))

    def test_snapshot_databases_have_structural_and_foreign_key_integrity(self):
        for name, connection in (("chat", self.chat), ("memory", self.memory)):
            with self.subTest(database=name):
                self.assertEqual([tuple(row) for row in connection.execute("PRAGMA integrity_check")], [("ok",)])
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
