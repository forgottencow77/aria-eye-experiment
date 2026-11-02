import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiment_engine import (
    CONDITIONS,
    DEFAULT_CONFIG,
    EMOTION_IDS,
    EVENT_ORDER,
    ExperimentError,
    ExperimentStore,
    _visual_state,
    generate_trials,
)


class Clock:
    def __init__(self):
        self.seconds = 0.0

    def advance(self, seconds=0.5):
        self.seconds += seconds

    def wall(self):
        whole = int(self.seconds)
        millis = int((self.seconds - whole) * 1000)
        return f"2026-10-29T00:00:{whole:02d}.{millis:03d}Z"

    def monotonic(self):
        return self.seconds


class ExperimentEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.store = ExperimentStore(self.temp.name, self.clock.wall, self.clock.monotonic)

    def tearDown(self):
        self.temp.cleanup()

    def create_running(self, mode="pilot", seed=17):
        session, token = self.store.create("P001", mode, DEFAULT_CONFIG, seed)
        self.store.consent(session["session_id"], token, True)
        self.store.start(session["session_id"], token)
        return session["session_id"], token

    def complete_current(self, session_id, token, emotion="anger", confidence=3):
        self.store.begin_trial(session_id, token)
        for event in EVENT_ORDER:
            self.store.event(session_id, token, event)
            self.clock.advance(0.5)
        return self.store.answer(session_id, token, emotion, confidence, 1234)

    def test_failed_presentation_requires_replay_before_answer(self):
        sid, token = self.create_running()
        self.store.begin_trial(sid, token)
        for event in EVENT_ORDER:
            self.store.event(sid, token, event)
        self.store.record_error(sid, token, "lease lost")
        with self.assertRaises(ExperimentError):
            self.store.answer(sid, token, "anger", 3, 1000)
        with self.assertRaises(ExperimentError):
            self.store.event(sid, token, "response")
        stimulus = self.store.begin_trial(sid, token, replay=True)
        self.assertEqual(stimulus["attempt"], 2)
        for event in EVENT_ORDER:
            self.store.event(sid, token, event)
        self.store.answer(sid, token, "anger", 3, 1000)
        self.assertEqual(self.store._session(sid)["practice"]["errors"][0]["attempt"], 1)

    def test_hardware_failure_invalidates_attempt_even_without_client_error_post(self):
        sid, token = self.create_running()
        self.store._session(sid)["hardware_mode"] = "hardware"
        self.store.begin_trial(sid, token)
        def disconnected(*args):
            raise RuntimeError("lease lost")
        with self.assertRaises(RuntimeError):
            self.store.event(sid, token, "instruction", disconnected)
        self.assertEqual(self.store._session(sid)["current_trial"]["phase"], "error")
        self.assertEqual(self.store._session(sid)["practice"]["status"], "invalid")
        with self.assertRaises(ExperimentError):
            self.store.answer(sid, token, "anger", 3, 1000)

    def test_latest_confirmed_defaults_are_used_and_old_candidate_values_are_not(self):
        self.assertEqual(DEFAULT_CONFIG["emotions"]["anger"]["gaze_vertical_deg"], 0)
        self.assertEqual(DEFAULT_CONFIG["emotions"]["disgust"]["gaze_horizontal_deg"], 18)
        self.assertEqual(DEFAULT_CONFIG["emotions"]["disgust"]["gaze_vertical_deg"], -20)
        self.assertEqual(DEFAULT_CONFIG["emotions"]["fear"]["eyelid_open_percent"], 80)
        self.assertEqual(DEFAULT_CONFIG["emotions"]["fear"]["gaze_vertical_deg"], 5)
        self.assertEqual(DEFAULT_CONFIG["emotions"]["joy"]["gaze_vertical_deg"], -20)
        self.assertEqual(DEFAULT_CONFIG["emotions"]["surprise"]["eyelid_open_percent"], 0)
        self.assertEqual(DEFAULT_CONFIG["emotions"]["disgust"]["eyelid_open_percent"], 55)
        self.assertEqual(DEFAULT_CONFIG["neutral"]["gaze_vertical_deg"], -8)
        self.assertEqual(DEFAULT_CONFIG["neutral"]["eyelid_open_percent"], 40)

    def test_factor_combinations_use_baseline_for_unselected_elements(self):
        gaze_only = _visual_state(DEFAULT_CONFIG, "fear", "G")
        self.assertEqual(gaze_only["iris_hex"], DEFAULT_CONFIG["neutral"]["iris_hex"])
        self.assertEqual(gaze_only["eyelid_open_percent"], DEFAULT_CONFIG["neutral"]["eyelid_open_percent"])
        self.assertEqual(gaze_only["gaze_vertical_deg"], 5)
        combined = _visual_state(DEFAULT_CONFIG, "fear", "LG")
        self.assertEqual(combined["iris_hex"], DEFAULT_CONFIG["neutral"]["iris_hex"])
        self.assertEqual(combined["eyelid_open_percent"], 80)
        self.assertEqual(combined["gaze_horizontal_deg"], 20)

    def test_42_slots_are_complete_deterministic_and_constrained(self):
        first = generate_trials(DEFAULT_CONFIG, 17)
        second = generate_trials(DEFAULT_CONFIG, 17)
        self.assertEqual([item["trial_slot_id"] for item in first], [item["trial_slot_id"] for item in second])
        self.assertEqual(len(first), 42)
        self.assertEqual(len({item["trial_slot_id"] for item in first}), 42)
        self.assertEqual({item["trial_slot_id"] for item in first}, {f"{emotion}__{factor}" for emotion in EMOTION_IDS for factor in CONDITIONS})
        self.assertFalse(any(first[i]["intended_emotion"] == first[i + 1]["intended_emotion"] == first[i + 2]["intended_emotion"] for i in range(40)))
        self.assertFalse(any(first[i]["physical_state_id"] == first[i + 1]["physical_state_id"] for i in range(41)))
        self.assertEqual(first, second)

    def test_physical_duplicates_are_detected_without_collapsing_slots(self):
        trials = generate_trials(DEFAULT_CONFIG, 5)
        duplicates = ExperimentStore.duplicate_states(trials)
        duplicate_groups = {frozenset(group["trial_slot_ids"]) for group in duplicates}
        self.assertEqual(duplicate_groups, {
            frozenset({"anger__L", "disgust__L"}),
            frozenset({"anger__G", "surprise__G"}),
            frozenset({"fear__L", "sadness__L"}),
        })
        self.assertEqual(len({trial["physical_state_id"] for trial in trials}), 39)
        self.assertEqual(len(trials), 42)

    def test_double_start_is_rejected_and_explicit_replay_is_recorded(self):
        session_id, token = self.create_running()
        initial = self.store.begin_trial(session_id, token)
        self.assertEqual(initial["kind"], "practice")
        with self.assertRaises(ExperimentError):
            self.store.begin_trial(session_id, token)
        replay = self.store.begin_trial(session_id, token, replay=True)
        self.assertEqual(replay["attempt"], 2)
        self.assertNotIn("intended_emotion", self.store.participant_view(session_id, token)["active"])

    def test_participant_access_token_is_in_fragment_not_http_query(self):
        session, token = self.store.create("P003", "pilot", DEFAULT_CONFIG, 71)
        parts = urlsplit(session["participant_url"])
        self.assertNotIn("access=", parts.query)
        self.assertEqual(parts.fragment, f"access={token}")

    def test_other_answer_skips_confidence_and_consent_cannot_be_bypassed(self):
        session, token = self.store.create("P002", "main", DEFAULT_CONFIG, 33)
        with self.assertRaises(ExperimentError):
            self.store.start(session["session_id"], token)
        self.store.consent(session["session_id"], token, True)
        self.store.start(session["session_id"], token)
        for event in EVENT_ORDER:
            self.store.begin_trial(session["session_id"], token) if event == EVENT_ORDER[0] else None
            self.store.event(session["session_id"], token, event)
        result = self.store.answer(session["session_id"], token, "other", None, 900)
        self.assertTrue(result["practice_done"])
        with self.assertRaises(ExperimentError):
            self.store.answer(session["session_id"], token, "other", 3, 900)

    def test_full_mock_session_persists_42_rows_and_separates_practice(self):
        session_id, token = self.create_running("pilot", 1234)
        practice = self.complete_current(session_id, token)
        self.assertEqual(practice["progress"]["completed"], 0)
        self.assertTrue(practice["practice_done"])
        for index in range(42):
            if index in (14, 28):
                self.store.confirm_break(session_id, token)
            emotion = "other" if index == 8 else "anger"
            confidence = None if emotion == "other" else 2
            self.store.begin_trial(session_id, token)
            for event in EVENT_ORDER:
                self.store.event(session_id, token, event)
                self.clock.advance(0.5)
            self.store.answer(session_id, token, emotion, confidence, 1000 + index)
        exported = self.store.json_export(session_id)
        self.assertEqual(exported["status"], "completed")
        self.assertEqual(exported["mode"], "pilot")
        self.assertEqual(exported["random_seed"], "1234")
        self.assertEqual(exported["hardware_mode"], "mock")
        self.assertTrue(exported["practice_response"]["excluded_from_analysis"])
        self.assertEqual(len(exported["trials"]), 42)
        self.assertEqual(len({trial["trial_slot_id"] for trial in exported["trials"]}), 42)
        self.assertTrue(all(trial["status"] == "answered" for trial in exported["trials"]))
        self.assertEqual(exported["trials"][8]["response"]["confidence"], None)
        self.assertEqual(len(exported["breaks_confirmed"]), 2)
        rows = list(csv.DictReader(io.StringIO(self.store.csv_export(session_id))))
        self.assertEqual(len(rows), 42)
        self.assertTrue(all(row["is_practice"] == "False" for row in rows))
        self.assertTrue(json.loads(rows[0]["timing_events"])[0]["monotonic_s"] >= 0)
        reloaded = ExperimentStore(self.temp.name, self.clock.wall, self.clock.monotonic)
        self.assertEqual(reloaded.json_export(session_id)["status"], "completed")

    def test_interruption_keeps_invalid_trial_and_reason(self):
        session_id, token = self.create_running()
        self.complete_current(session_id, token)
        self.store.begin_trial(session_id, token)
        self.store.event(session_id, token, "instruction")
        result = self.store.abort(session_id, token, "participant_exit")
        self.assertEqual(result["status"], "aborted")
        trial = self.store.json_export(session_id)["trials"][0]
        self.assertEqual(trial["status"], "invalid")
        self.assertEqual(trial["error"], "participant_exit")


if __name__ == "__main__":
    unittest.main()
