"""Check that further pruning preserves final structures and active references."""

import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unittest


ROOT = Path(__file__).resolve().parents[2]
PROJECTS = {"Demo-GPT": "structure-minimize-20261021-220732-c86c47"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def connect(project):
    db = sqlite3.connect((project / "memory/index.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    return db


class ShowcasePruningTests(unittest.TestCase):
    def test_final_structures_and_scientific_snapshots_are_preserved(self):
        for name, batch in PROJECTS.items():
            with self.subTest(project=name):
                project = ROOT / "projects" / name
                base = project / "artifacts/lammps/structure_minimize" / batch
                self.assertTrue((project / "chat/transcript.md").is_file())
                self.assertEqual(len(list(base.glob("*_minimized.cfg"))), 18)
                self.assertEqual(len(list(base.glob("*_minimized.lmp"))), 18)
                self.assertEqual(list((base / "items").glob("*/minimized.cfg")), [])
                self.assertEqual(list((base / "items").glob("*/minimized.lmp")), [])
                self.assertEqual(list(project.rglob("*.lammpstrj")), [])
                self.assertFalse((project / "runtime/checkpoints.sqlite").exists())
                shear = project / "artifacts/lammps/shear"
                self.assertEqual(len(list(shear.glob("*/structures/gb_shear_step_*.cfg"))), 212)
                neb = project / "artifacts/lammps/neb/neb-20261021-214036-574393"
                self.assertEqual(len(list(neb.glob("neb.replica.*.final.lmp"))), 18)

    def test_removed_files_are_unregistered_and_their_replacements_resolve(self):
        for name in PROJECTS:
            project = ROOT / "projects" / name
            with self.subTest(project=name), closing(connect(project)) as db:
                artifacts = {row["artifact_id"]: dict(row) for row in db.execute("SELECT * FROM artifacts")}
                records = []
                for run in db.execute("SELECT * FROM runs"):
                    base = Path(run["manifest_path"]).parent
                    for record in read_json(project / run["manifest_path"]).get("pruned_from_demo", []):
                        path = record.get("project_path") or record["path"]
                        if not path.startswith("artifacts/"):
                            path = (base / path).as_posix()
                        self.assertFalse((project / path).exists(), path)
                        if record.get("artifact_id"):
                            self.assertNotIn(record["artifact_id"], artifacts)
                        replacement = artifacts[record["replacement_artifact_id"]]
                        self.assertEqual(record["replacement_path"], replacement["path"])
                        self.assertEqual(record["replacement_run_id"], replacement["run_id"])
                        self.assertTrue((project / replacement["path"]).is_file())
                        kind = record.get("kind", record.get("type", ""))
                        if record.get("byte_identical") or kind.startswith(("duplicate_", "redundant_")):
                            self.assertEqual(record["original_sha256"], hashlib.sha256((project / replacement["path"]).read_bytes()).hexdigest())
                            self.assertEqual(record["original_size_bytes"], replacement["size_bytes"])
                        records.append(record)
                policy = read_json(project / "project.json")["showcase"]["file_selection"]["additional_cleanup"]
                self.assertEqual(len(records) + len(policy.get("omitted_runtime_files", [])), policy["removed_count"])
                self.assertEqual(policy["retained_batch_final_count"], 18)

    def test_active_json_references_resolve_after_staging_inputs_are_removed(self):
        for name in PROJECTS:
            project = ROOT / "projects" / name
            with self.subTest(project=name), closing(connect(project)) as db:
                artifact_ids = {row[0] for row in db.execute("SELECT artifact_id FROM artifacts")}

                def check(value, location):
                    if isinstance(value, dict):
                        for key, child in value.items():
                            if key in {"pruned_from_demo", "omitted_from_demo", "deduplicated_from_demo"}:
                                continue
                            if (key == "artifact_id" or key.endswith("_artifact_id")) and isinstance(child, str) and child:
                                self.assertIn(child, artifact_ids, location + "." + key)
                            if key == "path" and isinstance(child, str) and child.startswith("artifacts/"):
                                self.assertTrue((project / child).is_file(), location + ": " + child)
                            check(child, location + "." + key)
                    elif isinstance(value, list):
                        for index, child in enumerate(value):
                            check(child, location + f"[{index}]")

                for table, columns in (
                    ("runs", ("parameters_json", "progress_json", "result_json")),
                    ("facts", ("value_json",)),
                    ("artifacts", ("metadata_json",)),
                    ("run_sources", ("metadata_json",)),
                ):
                    for row in db.execute("SELECT * FROM " + table):
                        for column in columns:
                            check(json.loads(row[column]), table + "." + column)
                for path in (project / "artifacts").rglob("*.json"):
                    if path.name != "manifest.json":
                        check(read_json(path), str(path.relative_to(project)))


if __name__ == "__main__":
    unittest.main()
