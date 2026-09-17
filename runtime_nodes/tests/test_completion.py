"""CPU-only contract tests; no ComfyUI, model, or torch imports required."""

import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock


source = Path(__file__).resolve().parents[1] / "boogie_yue2" / "__init__.py"
spec = importlib.util.spec_from_file_location("boogie_yue2", source)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.clip = Mock()
        self.clip.decode.return_value = 'X:1\nK:C\n"C" C D E F |'
        self.metadata = {"yue2_truncated": False, "yue2_frames": 1549,
                         "yue2_abc_ids": [4, 15, 92]}

    def report(self, **kwargs):
        arguments = {"conditioning": [[object(), self.metadata]], "clip": self.clip,
                     "planning_generated": True, "abc_truncated": False}
        arguments.update(kwargs)
        output = bridge.BoogieYuE2Report().report(**arguments)
        return json.loads(output["ui"]["text"][0])

    def test_natural_end_reports_actual_frames_and_score(self):
        self.assertEqual(self.report(), {
            "schema_version": 1, "semantic_truncated": False, "frames": 1549,
            "seconds": 61.96, "abc": self.clip.decode.return_value,
            "abc_truncated": False,
        })
        self.clip.decode.assert_called_once_with([4, 15, 92])

    def test_music_cap_preserved_independently_of_abc_completion(self):
        self.metadata.update(yue2_truncated=True, yue2_frames=750)
        result = self.report()
        self.assertIs(result["semantic_truncated"], True)
        self.assertIs(result["abc_truncated"], False)
        self.assertEqual(result["seconds"], 30.0)

    def test_plan_cap_preserved_even_when_music_reaches_eos(self):
        result = self.report(abc_truncated=True)
        self.assertIs(result["semantic_truncated"], False)
        self.assertIs(result["abc_truncated"], True)

    def test_manual_score_does_not_infer_completion_from_retokenized_length(self):
        self.metadata["yue2_abc_ids"] = [7] * 20000
        result = self.report(planning_generated=False)
        self.assertIs(result["abc_truncated"], False)

    def test_off_mode_has_empty_score(self):
        self.metadata["yue2_abc_ids"] = []
        self.clip.decode.return_value = ""
        self.assertEqual(self.report(planning_generated=False)["abc"], "")

    def test_missing_or_wrong_metadata_fails_closed(self):
        for key, values in {
            "yue2_truncated": [None, 0, 1, "false"],
            "yue2_frames": [None, True, 0, -1, 2.5, "25"],
            "yue2_abc_ids": [None, "123", [True], [-1], [1.0]],
        }.items():
            saved = self.metadata[key]
            for invalid in values:
                with self.subTest(key=key, invalid=invalid):
                    self.metadata[key] = invalid
                    with self.assertRaises(ValueError):
                        self.report()
            del self.metadata[key]
            with self.assertRaises(ValueError):
                self.report()
            self.metadata[key] = saved

    def test_ambiguous_conditioning_fails_closed(self):
        for invalid in [None, [], [None], [[None]], [[None, {}], [None, {}]]]:
            with self.subTest(conditioning=invalid), self.assertRaises(ValueError):
                self.report(conditioning=invalid)

    def test_generated_plan_missing_from_conditioning_fails_closed(self):
        self.metadata["yue2_abc_ids"] = []
        with self.assertRaises(ValueError):
            self.report()

    def test_wrong_planning_flags_fail_closed(self):
        for args in [{"planning_generated": "true"}, {"abc_truncated": 1},
                     {"planning_generated": False, "abc_truncated": True}]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.report(**args)


class ABCGenerationTests(unittest.TestCase):
    def setUp(self):
        self.clip = Mock()
        self.clip.tokenize.return_value = {"prefix": [11, 12], "seed": 17}
        self.clip.decode.return_value = "score whose decoded length differs from raw IDs"

    def generate(self, count, **kwargs):
        self.clip.generate.return_value = list(range(count))
        return bridge.BoogieYuE2GenerateABC().generate_abc(
            self.clip, "country pop", "original lyrics", 17, "melody", 64, **kwargs)

    def test_eos_before_budget_is_complete(self):
        abc, truncated = self.generate(63)
        self.assertEqual(abc, self.clip.decode.return_value)
        self.assertIs(truncated, False)

    def test_exact_raw_budget_marks_truncated_before_decode(self):
        abc, truncated = self.generate(64)
        self.assertIs(truncated, True)
        self.assertNotEqual(len(abc), 64)
        self.clip.decode.assert_called_once_with(list(range(64)))

    def test_matches_native_calls_and_all_sampling_controls(self):
        self.generate(50, temperature=0.55, top_p=0.85, top_k=27,
                      repetition_penalty=1.01, penalty_window=123)
        self.clip.tokenize.assert_called_once_with(
            "country pop", lyrics="original lyrics", cot="melody", seed=17,
            max_tokens=64, penalty_window=123)
        self.clip.generate.assert_called_once_with(
            self.clip.tokenize.return_value, max_length=64, temperature=0.55,
            top_p=0.85, top_k=27, repetition_penalty=1.01, seed=17)


class WorkflowContractTests(unittest.TestCase):
    def test_checked_in_graphs_report_completion(self):
        root = Path(__file__).resolve().parents[2]
        for planned in [False, True]:
            name = "yue2-planned-api.json" if planned else "yue2-api.json"
            graph = json.loads((root / "workflows" / name).read_text())
            self.assertEqual(graph["9"]["class_type"], "BoogieYuE2Report")
            inputs = graph["9"]["inputs"]
            self.assertEqual(inputs["conditioning"], ["2", 0])
            self.assertEqual(inputs["clip"], ["1", 1])
            self.assertIs(inputs["planning_generated"], planned)
            self.assertEqual(inputs["abc_truncated"], ["8", 1] if planned else False)
            self.assertEqual(graph["4"]["inputs"]["seconds"], ["2", 1])
            if planned:
                self.assertEqual(graph["8"]["class_type"], "BoogieYuE2GenerateABC")
                self.assertEqual(graph["2"]["inputs"]["abc"], ["8", 0])


if __name__ == "__main__":
    unittest.main()
