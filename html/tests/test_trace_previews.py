"""Regression checks for the recorded tool-input and tool-output previews."""

import importlib.util
import json
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_demo.py"
SPEC = importlib.util.spec_from_file_location("build_demo_trace_previews", SCRIPT)
DEMO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEMO)

DELEGATE_TOOLS = (
    "delegate_plan",
    "delegate_csl",
    "delegate_lammps",
    "delegate_analysis",
    "delegate_coding",
)


class TracePreviewTests(unittest.TestCase):
    def test_ordinary_input_character_boundaries(self):
        for length in (279, 280, 281, 700):
            with self.subTest(length=length):
                value = "x" * length
                expected = value if length <= 280 else "x" * 279 + "…"
                self.assertEqual(DEMO.preview_tool_input(value), expected)
                self.assertLessEqual(len(DEMO.preview_tool_input(value)), 280)

    def test_ordinary_output_character_boundaries(self):
        for length in (399, 400, 401, 900):
            with self.subTest(length=length):
                value = "x" * length
                expected = value if length <= 400 else "x" * 399 + "…"
                self.assertEqual(DEMO.preview_tool_output(value), expected)
                self.assertLessEqual(len(DEMO.preview_tool_output(value)), 400)

    def test_delegate_input_character_boundaries(self):
        for tool in DELEGATE_TOOLS:
            for length in (1199, 1200, 1201, 2000):
                with self.subTest(tool=tool, length=length):
                    task = "x" * length
                    value = json.dumps({"task": task, "ignored": "metadata"})
                    expected = task if length <= 1200 else "x" * 1199 + "…"
                    self.assertEqual(DEMO.preview_tool_input(value, tool), expected)

    def test_delegate_output_character_boundaries(self):
        for tool in DELEGATE_TOOLS:
            for length in (7999, 8000, 8001, 10000):
                with self.subTest(tool=tool, length=length):
                    summary = "x" * length
                    value = json.dumps({"summary": summary, "ignored": "metadata"})
                    expected = summary if length <= 8000 else "x" * 7999 + "…"
                    self.assertEqual(DEMO.preview_tool_output(value, tool), expected)

    def test_ordinary_whitespace_is_stripped_before_measurement(self):
        for function, limit in ((DEMO.preview_tool_input, 280), (DEMO.preview_tool_output, 400)):
            with self.subTest(function=function.__name__):
                self.assertEqual(function(" \t\r\n "), "")
                self.assertEqual(function(" \nalpha\n beta\t "), "alpha\n beta")
                self.assertEqual(function(" \n" + "x" * limit + " \t"), "x" * limit)

    def test_delegate_task_whitespace_is_stripped(self):
        value = json.dumps({"task": " \t\n晶界 task 🙂 \r\n "}, ensure_ascii=False)
        self.assertEqual(DEMO.preview_tool_input(" \n" + value + "\t ", "delegate_analysis"), "晶界 task 🙂")

    def test_empty_delegate_task_falls_back_to_original_input(self):
        value = '{ "task" : " \\n\\t ", "coordinates" : [' + "1," * 250 + "0] }"
        self.assertEqual(DEMO.preview_tool_input(" \n" + value + "\t ", "delegate_csl"), value[:279] + "…")

    def test_unicode_and_emoji_boundaries_count_characters(self):
        value = "汉🙂" * 500
        self.assertEqual(DEMO.preview_tool_input(" \n" + value + "\t "), value[:279] + "…")
        self.assertEqual(DEMO.preview_tool_output(" \n" + value + "\t "), value[:399] + "…")
        task = "晶界🧪" * 500
        self.assertEqual(
            DEMO.preview_tool_input(json.dumps({"task": task}, ensure_ascii=False), "delegate_lammps"),
            task[:1199] + "…",
        )
        summary = "原子🙂" * 3000
        self.assertEqual(
            DEMO.preview_tool_output(json.dumps({"summary": summary}, ensure_ascii=False), "delegate_analysis"),
            summary[:7999] + "…",
        )

    def test_delegate_output_prefers_top_level_summary(self):
        value = json.dumps({"summary": " \nTop summary 🙂 \t", "result": {"summary": "Nested summary"}, "error": "Error"})
        self.assertEqual(DEMO.preview_tool_output(value, "delegate_plan"), "Top summary 🙂")

    def test_delegate_output_uses_nonempty_nested_summary(self):
        for top_summary in (None, "", " \n\t "):
            with self.subTest(top_summary=top_summary):
                value = json.dumps({"summary": top_summary, "result": {"summary": " \nNested summary 中文 \t"}, "error": "Error"})
                self.assertEqual(DEMO.preview_tool_output(value, "delegate_coding"), "Nested summary 中文")

    def test_delegate_output_falls_back_to_nonempty_error(self):
        value = json.dumps({"summary": " \n ", "result": {"summary": "\t "}, "error": " \nRecorded failure 🙂 \t "})
        self.assertEqual(DEMO.preview_tool_output(value, "delegate_lammps"), "Recorded failure 🙂")
        self.assertEqual(DEMO.preview_tool_output('{"error": " Failure "}', "delegate_csl"), "Failure")

    def test_empty_delegate_summary_and_error_use_ordinary_limit(self):
        value = '{ "summary" : "  ", "result" : { "summary" : "\\t" }, "error" : "", "data" : "' + "q" * 900 + '" }'
        self.assertEqual(DEMO.preview_tool_output(value, "delegate_analysis"), value[:399] + "…")

    def test_unknown_delegate_names_use_ordinary_limits(self):
        input_value = json.dumps({"task": "x" * 2000})
        output_value = json.dumps({"summary": "x" * 10000})
        for tool in ("delegate_unknown", "delegate_analysis_extra", "Delegate_analysis", "delegate_analysis ", "delegate_code", "read_file", None):
            with self.subTest(tool=tool):
                self.assertEqual(DEMO.preview_tool_input(input_value, tool), input_value[:279] + "…")
                self.assertEqual(DEMO.preview_tool_output(output_value, tool), output_value[:399] + "…")

    def test_json_fallback_preserves_original_prefix_and_key_order(self):
        value = '{ "z_key" : [ 3, 2, 1 ], "a_key" : "' + "v" * 900 + '" }'
        self.assertEqual(DEMO.preview_tool_input(" \n" + value + "\n ", "read_file"), value[:279] + "…")
        self.assertEqual(DEMO.preview_tool_output(" \n" + value + "\n ", "read_file"), value[:399] + "…")
        self.assertEqual(DEMO.preview_tool_input(value, "delegate_csl"), value[:279] + "…")
        self.assertEqual(DEMO.preview_tool_output(value, "delegate_csl"), value[:399] + "…")

    def test_delegate_malformed_json_uses_ordinary_preview(self):
        value = '{"task": "unfinished ' + "x" * 1000
        self.assertEqual(DEMO.preview_tool_input(value, "delegate_plan"), value[:279] + "…")
        self.assertEqual(DEMO.preview_tool_output(value, "delegate_plan"), value[:399] + "…")

    def test_delegate_nonobject_json_uses_ordinary_preview(self):
        value = json.dumps([{"task": "x" * 2000, "summary": "x" * 10000}])
        self.assertEqual(DEMO.preview_tool_input(value, "delegate_analysis"), value[:279] + "…")
        self.assertEqual(DEMO.preview_tool_output(value, "delegate_analysis"), value[:399] + "…")


if __name__ == "__main__":
    unittest.main()
