import copy
import itertools
import json
import unittest

import app as automata


class AutomataTests(unittest.TestCase):
    def setUp(self):
        self.even = automata.canonical_preset_by_slug("even-a")["dfa"]

    def test_equivalent_after_state_renaming(self):
        rename = {"q0": "even", "q1": "odd"}
        renamed = {
            "states": list(rename.values()), "alphabet": ["a", "b"],
            "start": "even", "accepting": ["even"],
            "transitions": {
                rename[s]: {c: rename[t] for c, t in edges.items()}
                for s, edges in self.even["transitions"].items()
            },
        }
        self.assertIsNone(automata.shortest_counterexample(self.even, renamed))

    def test_shortest_counterexample_and_trace(self):
        wrong = copy.deepcopy(self.even)
        wrong["transitions"]["q1"]["a"] = "q1"
        self.assertEqual(automata.shortest_counterexample(self.even, wrong), "aa")
        result = automata.evaluate_submission(wrong, self.even)
        self.assertFalse(result["equivalent"])
        self.assertFalse(result["counterexample_trace"]["answer"]["accepted"])
        self.assertTrue(result["counterexample_trace"]["target"]["accepted"])

    def test_empty_word_counterexample(self):
        wrong = copy.deepcopy(self.even)
        wrong["accepting"] = ["q1"]
        self.assertEqual(automata.shortest_counterexample(self.even, wrong), "ε")

    def test_missing_transition_is_reported(self):
        incomplete = copy.deepcopy(self.even)
        del incomplete["transitions"]["q0"]["b"]
        result = automata.evaluate_submission(incomplete, self.even)
        self.assertFalse(result["deterministic"])
        self.assertEqual(result["counterexample"], "b")

    def test_different_alphabets_have_a_rejecting_counterexample_trace(self):
        restricted = copy.deepcopy(self.even)
        restricted["alphabet"] = ["a"]
        for edges in restricted["transitions"].values():
            edges.pop("b")
        result = automata.evaluate_submission(restricted, self.even)
        self.assertFalse(result["equivalent"])
        self.assertEqual(result["counterexample"], "b")
        self.assertFalse(result["counterexample_trace"]["answer"]["accepted"])
        self.assertTrue(result["counterexample_trace"]["target"]["accepted"])
        self.assertEqual(result["counterexample_trace"]["answer"]["steps"][-1]["symbol"], "b")

    def test_duplicate_transition_is_rejected(self):
        _, errors = automata.parse_transitions_text("q0,a=q1\nq0,a=q0")
        self.assertTrue(any("duplicate" in error for error in errors))

    def test_minimization_reports_mergeable_states(self):
        redundant = {
            "states": ["q0", "q1", "q2"], "alphabet": ["a", "b"],
            "start": "q0", "accepting": ["q0", "q2"],
            "transitions": {
                "q0": {"a": "q1", "b": "q2"},
                "q1": {"a": "q0", "b": "q1"},
                "q2": {"a": "q1", "b": "q2"},
            },
        }
        result = automata.evaluate_submission(redundant, self.even)
        self.assertTrue(result["equivalent"])
        self.assertFalse(result["minimal"])
        self.assertEqual(result["minimized_state_count"], 2)
        self.assertIn(["q0", "q2"], result["merge_groups"])
        self.assertTrue(result["partition_snapshots"])

    def test_unreachable_state_is_not_minimal(self):
        redundant = copy.deepcopy(self.even)
        redundant["states"].append("unused")
        redundant["transitions"]["unused"] = {"a": "unused", "b": "unused"}
        minimal, reduced = automata.is_minimal(redundant)
        self.assertFalse(minimal)
        self.assertEqual(len(reduced["states"]), 2)

    def test_regex_language_against_expected_examples(self):
        preview, errors = automata.source_preview_payload("regex", "(a|b)*ab")
        self.assertEqual(errors, [])
        for length in range(6):
            for chars in itertools.product("ab", repeat=length):
                word = "".join(chars)
                with self.subTest(word=word):
                    self.assertEqual(
                        automata.trace_word(preview["dfa"], word)["accepted"],
                        word.endswith("ab"),
                    )

    def test_epsilon_nfa_subset_construction(self):
        nfa = {
            "states": ["s0", "s1"], "alphabet": ["a"],
            "start": "s0", "accepting": ["s1"],
            "transitions": {"s0": {"": ["s1"]}, "s1": {"a": ["s1"]}},
        }
        preview, errors = automata.source_preview_payload("nfa", json.dumps(nfa))
        self.assertEqual(errors, [])
        for word in ["", "a", "aaaa"]:
            self.assertTrue(automata.trace_word(preview["dfa"], word)["accepted"])

    def test_invalid_regex_returns_feedback(self):
        preview, errors = automata.source_preview_payload("regex", "(a|b")
        self.assertIsNone(preview)
        self.assertTrue(errors)

    def test_transition_delimiters_are_unambiguous(self):
        for text in ['q0,a=q1=extra', 'q0,a,b=q1', 'q0,a=q1,extra']:
            with self.subTest(text=text):
                transitions, errors = automata.parse_transitions_text(text)
                self.assertEqual(transitions, {})
                self.assertTrue(errors)

    def test_alphabet_rejects_multi_character_and_reserved_symbols(self):
        for symbol in ['ab', 'ε', '=', ';']:
            with self.subTest(symbol=symbol):
                _, errors = automata.build_dfa_from_form({'states':'q0', 'start':'q0', 'alphabet':symbol})
                self.assertTrue(any('single characters' in error for error in errors))

    def test_epsilon_only_dfa_can_be_submitted(self):
        preview, errors = automata.source_preview_payload('regex', 'ε')
        self.assertEqual(errors, [])
        dfa, errors = automata.build_dfa_from_form(automata.dfa_to_initial_form(preview['dfa']))
        self.assertEqual(errors, [])
        self.assertEqual(dfa['alphabet'], [])
        result = automata.evaluate_submission(dfa, preview['dfa'])
        self.assertTrue(result['equivalent'])
        self.assertTrue(result['minimal'])

    def test_invalid_nfa_structures_return_errors(self):
        bad = [[], None, {'states':None}, {'states':'q0'},
               {'states':['q0'], 'start':7}, {'states':['q0'], 'transitions':[]},
               {'states':['q0'], 'transitions':{'q0':[]}},
               {'states':['q0'], 'transitions':{'q0':{'a':None}}},
               {'states':['q0'], 'transitions':{'q0':{'a':[7]}}}]
        for raw in bad:
            with self.subTest(raw=raw):
                nfa, errors = automata.parse_nfa_json(json.dumps(raw))
                self.assertIsNone(nfa)
                self.assertTrue(errors)

    def test_nfa_supports_empty_alphabet_and_epsilon_edges(self):
        nfa, errors = automata.parse_nfa_json(json.dumps({
            'states':['q0','q1'], 'alphabet':[], 'start':'q0', 'accepting':['q1'],
            'transitions':{'q0':{'ε':['q1']}}
        }))
        self.assertEqual(errors, [])
        self.assertTrue(automata.trace_word(automata.nfa_to_dfa(nfa), '')['accepted'])

    def test_duplicate_states_and_delimited_names_are_rejected(self):
        for states in ['q0,q0', 'q0,q=1', 'q0,q;1']:
            _, errors = automata.build_dfa_from_form({'states':states, 'start':'q0', 'alphabet':'a'})
            self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
