"""Mock-only experiment session engine for the Aria eye UI.

This module deliberately has no dependency on the servo or LCD controllers.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import os
import random
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class ExperimentError(ValueError):
    pass


EMOTION_NAMES = {
    "anger": "怒っている",
    "disgust": "嫌がっている（嫌悪）",
    "fear": "怖がっている",
    "joy": "喜んでいる",
    "sadness": "悲しんでいる",
    "surprise": "驚いている",
    "neutral": "特に感情を感じない",
    "other": "その他・わからない",
}
EMOTION_IDS = ("anger", "disgust", "fear", "joy", "sadness", "surprise")
FACTORS = ("C", "L", "G")
CONDITIONS = ("C", "L", "G", "CL", "CG", "LG", "CLG")
EVENT_ORDER = ("instruction", "baseline", "transition", "target", "return", "response")
DEFAULT_CONFIG = {
    "neutral": {
        "iris_hex": "#BFBFBF", "eyelid_open_percent": 40,
        "gaze_horizontal_deg": 0, "gaze_vertical_deg": -8,
    },
    "emotions": {
        "anger": {"iris_hex": "#BF3939", "eyelid_open_percent": 55, "gaze_horizontal_deg": 0, "gaze_vertical_deg": 0},
        "disgust": {"iris_hex": "#50BF39", "eyelid_open_percent": 55, "gaze_horizontal_deg": 18, "gaze_vertical_deg": -20},
        "fear": {"iris_hex": "#6639BF", "eyelid_open_percent": 80, "gaze_horizontal_deg": 20, "gaze_vertical_deg": 5},
        "joy": {"iris_hex": "#BF9E39", "eyelid_open_percent": 31, "gaze_horizontal_deg": 0, "gaze_vertical_deg": -20},
        "sadness": {"iris_hex": "#3966BF", "eyelid_open_percent": 80, "gaze_horizontal_deg": 0, "gaze_vertical_deg": 5},
        "surprise": {"iris_hex": "#39B4BF", "eyelid_open_percent": 0, "gaze_horizontal_deg": 0, "gaze_vertical_deg": 0},
    },
}
# The previously confirmed settings are retained here for the admin API snapshot.
SPEC_SNAPSHOT = {
    "neutral": {"iris_hex": "#BFBFBF", "eyelid_open_percent": 65, "gaze_horizontal_deg": 0, "gaze_vertical_deg": -8},
    "emotions": {
        "anger": {"iris_hex": "#BF3939", "eyelid_open_percent": 55, "gaze_horizontal_deg": 0, "gaze_vertical_deg": 0},
        "disgust": {"iris_hex": "#50BF39", "eyelid_open_percent": 55, "gaze_horizontal_deg": 18, "gaze_vertical_deg": -20},
        "fear": {"iris_hex": "#6639BF", "eyelid_open_percent": 80, "gaze_horizontal_deg": 20, "gaze_vertical_deg": 5},
        "joy": {"iris_hex": "#BF9E39", "eyelid_open_percent": 31, "gaze_horizontal_deg": 0, "gaze_vertical_deg": -20},
        "sadness": {"iris_hex": "#3966BF", "eyelid_open_percent": 80, "gaze_horizontal_deg": 0, "gaze_vertical_deg": 5},
        "surprise": {"iris_hex": "#39B4BF", "eyelid_open_percent": 0, "gaze_horizontal_deg": 0, "gaze_vertical_deg": 0},
    },
}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _stable_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _number(value, label, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ExperimentError(f"{label} は有限の数値で指定してください。")
    if integer and int(value) != value:
        raise ExperimentError(f"{label} は整数で指定してください。")
    if value < low or value > high:
        raise ExperimentError(f"{label} は {low}〜{high} の範囲で指定してください。")
    return int(value) if integer else float(value)


def validate_config(config):
    if not isinstance(config, dict) or set(config) != {"neutral", "emotions"}:
        raise ExperimentError("設定は neutral と emotions を含むJSONオブジェクトにしてください。")
    if not isinstance(config["neutral"], dict) or not isinstance(config["emotions"], dict):
        raise ExperimentError("共通基準と感情設定の形式が不正です。")
    if set(config["emotions"]) != set(EMOTION_IDS):
        raise ExperimentError("6感情すべての設定が必要です。")
    result = {"neutral": {}, "emotions": {}}
    for key, source in [("neutral", config["neutral"])] + [(emotion, config["emotions"][emotion]) for emotion in EMOTION_IDS]:
        if not isinstance(source, dict):
            raise ExperimentError(f"{key} の設定形式が不正です。")
        color = source.get("iris_hex")
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ExperimentError(f"{key} の虹彩色は #RRGGBB 形式で指定してください。")
        row = {
            "iris_hex": color.upper(),
            "eyelid_open_percent": _number(source.get("eyelid_open_percent"), f"{key} 上瞼UI値", 0, 100, True),
            "gaze_horizontal_deg": _number(source.get("gaze_horizontal_deg"), f"{key} 視線左右UI値", -20, 20, True),
            "gaze_vertical_deg": _number(source.get("gaze_vertical_deg"), f"{key} 視線上下UI値", -20, 20, True),
        }
        if key == "neutral":
            result["neutral"] = row
        else:
            result["emotions"][key] = row
    return result


def _visual_state(config, emotion, factors):
    base = config["neutral"]
    chosen = config["emotions"][emotion]
    return {
        "iris_hex": chosen["iris_hex"] if "C" in factors else base["iris_hex"],
        "eyelid_open_percent": chosen["eyelid_open_percent"] if "L" in factors else base["eyelid_open_percent"],
        "gaze_horizontal_deg": chosen["gaze_horizontal_deg"] if "G" in factors else base["gaze_horizontal_deg"],
        "gaze_vertical_deg": chosen["gaze_vertical_deg"] if "G" in factors else base["gaze_vertical_deg"],
    }


def _physical_id(values):
    raw = _stable_json(values).encode("utf-8")
    value = 14695981039346656037
    for byte in raw:
        value = ((value ^ byte) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return "phys_" + format(value, "016x")


def generate_trials(config, seed):
    """Build 42 factor slots and randomized order with both adjacency constraints."""
    slots = []
    for emotion in EMOTION_IDS:
        for factors in CONDITIONS:
            values = _visual_state(config, emotion, factors)
            slots.append({
                "trial_slot_id": f"{emotion}__{factors}",
                "intended_emotion": emotion,
                "factors": list(factors),
                "actual_target_values": values,
                "physical_state_id": _physical_id(values),
            })
    rng = random.Random(seed)
    # Random retries are deterministic for a given seed. The constraints are loose
    # for this 42-slot design and 20,000 attempts leaves a generous safety margin.
    for _ in range(20_000):
        order = list(slots)
        rng.shuffle(order)
        if any(order[i]["intended_emotion"] == order[i + 1]["intended_emotion"] == order[i + 2]["intended_emotion"] for i in range(40)):
            continue
        if any(order[i]["physical_state_id"] == order[i + 1]["physical_state_id"] for i in range(41)):
            continue
        return order
    raise ExperimentError("提示順の制約を満たせませんでした。別の乱数シードで再試行してください。")


class ExperimentStore:
    def __init__(self, data_dir=None, wall_clock=utc_now, monotonic=time.monotonic):
        self.data_dir = Path(data_dir or os.environ.get("ARIA_EXPERIMENT_DATA_DIR", Path(__file__).resolve().parent / "experiment_data"))
        self.path = self.data_dir / "sessions.json"
        self.wall_clock = wall_clock
        self.monotonic = monotonic
        self.lock = threading.RLock()
        self.sessions = {}
        self.current_config = copy.deepcopy(DEFAULT_CONFIG)
        self.config_initialized = False
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                self.sessions = loaded.get("sessions", {})
                if "current_config" in loaded:
                    self.current_config = validate_config(loaded["current_config"])
                    self.config_initialized = bool(loaded.get("config_initialized", True))
            except (OSError, json.JSONDecodeError) as error:
                raise ExperimentError(f"実験データを読み込めません: {error}") from error

    def _save(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"schema_version": 1, "current_config": self.current_config,
                              "config_initialized": self.config_initialized,
                              "sessions": self.sessions}, ensure_ascii=False, indent=2)
        fd, temp_path = tempfile.mkstemp(prefix="sessions.", suffix=".tmp", dir=self.data_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
            try:
                dir_fd = os.open(self.data_dir, os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except (AttributeError, OSError):
                pass
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def create(self, participant_id, mode, config=None, seed=None, hardware_mode="mock"):
        if not isinstance(participant_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", participant_id):
            raise ExperimentError("参加者IDは個人名を含まない英数字・ハイフン・アンダースコアで入力してください。")
        if mode not in ("pilot", "main"):
            raise ExperimentError("セッション種別は pilot または main です。")
        if hardware_mode not in ("mock", "hardware"):
            raise ExperimentError("提示先は mock または hardware を指定してください。")
        config = validate_config(config if config is not None else self.current_config)
        if seed is None:
            seed = secrets.randbits(63)
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ExperimentError("random_seed は0以上の整数が必要です。")
        version_hash = hashlib.sha256(_stable_json(config).encode("utf-8")).hexdigest()
        order = generate_trials(config, seed)
        session_id = "aria_" + secrets.token_urlsafe(12)
        participant_token = secrets.token_urlsafe(24)
        session = {
            "session_id": session_id,
            "participant_id": participant_id,
            "mode": mode,
            "hardware_mode": hardware_mode,
            "status": "awaiting_consent",
            "created_at": self.wall_clock(),
            "started_at": None,
            "consent_at": None,
            "configuration_version": version_hash[:12],
            "configuration_hash": version_hash,
            "configuration": config,
            "random_seed": str(seed),
            "generated_order": [trial["trial_slot_id"] for trial in order],
            "presentation_order": [trial["trial_slot_id"] for trial in order],
            "trials": [dict(trial, presentation_index=index + 1, status="pending", attempts=[], response=None, error=None)
                       for index, trial in enumerate(order)],
            "practice": None,
            "practice_response": None,
            "next_index": 0,
            "current_trial": None,
            "breaks_confirmed": [],
            "free_text": None,
            "finished_at": None,
            "events": [],
            "_participant_token": participant_token,
        }
        with self.lock:
            self.current_config = copy.deepcopy(config)
            self.config_initialized = True
            self.sessions[session_id] = session
            self._save()
        return self.admin_view(session), participant_token

    def update_current_config(self, config):
        config = validate_config(config)
        with self.lock:
            self.current_config = copy.deepcopy(config)
            self.config_initialized = True
            self._save()
            return copy.deepcopy(self.current_config)

    def get_current_config(self):
        with self.lock:
            return copy.deepcopy(self.current_config)

    def get_config_state(self):
        with self.lock:
            return {"config": copy.deepcopy(self.current_config), "config_initialized": self.config_initialized}

    def config_version(self):
        return hashlib.sha256(_stable_json(self.get_current_config()).encode("utf-8")).hexdigest()[:12]

    def list_sessions(self):
        with self.lock:
            return [self.admin_view(session, include_trials=False) for session in sorted(self.sessions.values(), key=lambda item: item["created_at"], reverse=True)]

    @staticmethod
    def _public_trial(trial):
        result = copy.deepcopy(trial)
        result.pop("attempts", None)
        return result

    def admin_view(self, session, include_trials=True):
        view = copy.deepcopy(session)
        view.pop("_participant_token", None)
        view["participant_url"] = f"/experiment/participant?session={session['session_id']}#access={session['_participant_token']}"
        view["duplicate_physical_states"] = self.duplicate_states(session["trials"])
        if not include_trials:
            view.pop("trials", None)
            view.pop("configuration", None)
            view.pop("generated_order", None)
            view.pop("presentation_order", None)
        return view

    @staticmethod
    def duplicate_states(trials):
        groups = {}
        for trial in trials:
            groups.setdefault(trial["physical_state_id"], []).append(trial["trial_slot_id"])
        return [{"physical_state_id": key, "trial_slot_ids": values} for key, values in groups.items() if len(values) > 1]

    def _session(self, session_id, token=None):
        session = self.sessions.get(session_id)
        if not session:
            raise ExperimentError("セッションが見つかりません。")
        if token is not None and not secrets.compare_digest(str(session["_participant_token"]), str(token)):
            raise ExperimentError("参加者リンクが無効です。")
        return session

    def participant_view(self, session_id, token):
        with self.lock:
            session = self._session(session_id, token)
            active = None
            if session.get("current_trial"):
                current = session["current_trial"]
                response_event = next((item for item in reversed(current["events"])
                                       if item.get("event") == "response"), None)
                active = {
                    "kind": "practice" if current["kind"] == "practice" else "stimulus",
                    "phase": current["phase"],
                    "visual": copy.deepcopy(current["visual"]),
                    "baseline": copy.deepcopy(session["configuration"]["neutral"]),
                    "attempt": current["attempt"],
                    "response_elapsed_ms": self._elapsed_ms_since(response_event["at"]) if response_event else 0,
                }
            return {
                "ok": True, "session_id": session_id, "status": session["status"],
                "hardware_mode": session.get("hardware_mode", "mock"),
                "consented": session["consent_at"] is not None,
                "progress": {"completed": session["next_index"], "total": len(session["trials"])},
                "practice_done": session["practice_response"] is not None,
                "breaks_confirmed": list(session["breaks_confirmed"]),
                "active": active,
                "needs_break": session["next_index"] in (14, 28) and session["next_index"] not in session["breaks_confirmed"],
                "finished_at": session["finished_at"],
            }

    def _elapsed_ms_since(self, timestamp):
        try:
            start = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            now_value = self.wall_clock()
            now = now_value if isinstance(now_value, datetime) else datetime.fromisoformat(str(now_value).replace("Z", "+00:00"))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            elapsed = round((now - start).total_seconds() * 1000)
            return max(0, min(86_400_000, elapsed))
        except (TypeError, ValueError):
            return 0

    def consent(self, session_id, token, accepted):
        if not isinstance(accepted, bool):
            raise ExperimentError("同意の値が不正です。")
        with self.lock:
            session = self._session(session_id, token)
            if session["consent_at"]:
                raise ExperimentError("同意記録はすでに保存されています。")
            if not accepted:
                session["status"] = "declined"
                session["events"].append({"event": "consent_declined", "at": self.wall_clock()})
            else:
                session["consent_at"] = self.wall_clock()
                session["status"] = "consented"
                session["events"].append({"event": "consent_accepted", "at": session["consent_at"]})
            self._save()
            return self.participant_view(session_id, token)

    def start(self, session_id, token):
        with self.lock:
            session = self._session(session_id, token)
            if not session["consent_at"]:
                raise ExperimentError("同意を記録してから開始してください。")
            if session["status"] in ("completed", "aborted", "declined"):
                raise ExperimentError("このセッションは開始できません。")
            session["status"] = "running"
            if session["started_at"] is None:
                session["started_at"] = self.wall_clock()
                session["events"].append({"event": "session_started", "at": session["started_at"]})
            self._save()
            return self.participant_view(session_id, token)

    def begin_trial(self, session_id, token, replay=False):
        with self.lock:
            session = self._session(session_id, token)
            if not session["consent_at"] or session["status"] != "running":
                raise ExperimentError("同意後にセッションを開始してください。")
            if session["current_trial"]:
                if replay is not True:
                    raise ExperimentError("この試行はすでに進行中です。再提示操作が必要です。")
                current = session["current_trial"]
                trial = self._trial_for_current(session, current)
                current["attempt"] += 1
                current["phase"] = "instruction"
                current["phase_index"] = 0
                current["events"] = []
                current["started_monotonic"] = self.monotonic()
                trial["status"] = "presenting"
                trial["replayed"] = current["attempt"] > 1
                trial["attempt_count"] = current["attempt"]
                self._save()
                return self._stimulus_view(session, current)
            if session["next_index"] in (14, 28) and session["next_index"] not in session["breaks_confirmed"]:
                raise ExperimentError("休憩画面を確認してから続行してください。")
            if session["practice_response"] is None:
                if session["practice"] is None:
                    practice_values = {"iris_hex": "#9B6D72", "eyelid_open_percent": 47, "gaze_horizontal_deg": -8, "gaze_vertical_deg": 6}
                    session["practice"] = {"trial_slot_id": "practice", "actual_target_values": practice_values, "physical_state_id": _physical_id(practice_values), "status": "pending", "attempts": []}
                current = self._new_current("practice", session["practice"]["actual_target_values"])
                session["current_trial"] = current
                self._save()
                return self._stimulus_view(session, current)
            if session["next_index"] >= len(session["trials"]):
                raise ExperimentError("全試行が完了しています。")
            trial = session["trials"][session["next_index"]]
            current = self._new_current(trial["trial_slot_id"], trial["actual_target_values"])
            session["current_trial"] = current
            trial["status"] = "presenting"
            trial["replayed"] = current["attempt"] > 1
            trial["attempt_count"] = current["attempt"]
            self._save()
            return self._stimulus_view(session, current)

    def _new_current(self, kind, values):
        return {"kind": kind, "phase": "instruction", "phase_index": 0, "visual": copy.deepcopy(values),
                "attempt": 1, "events": [], "started_monotonic": self.monotonic()}

    @staticmethod
    def _trial_for_current(session, current):
        if current["kind"] == "practice":
            return session["practice"]
        for trial in session["trials"]:
            if trial["trial_slot_id"] == current["kind"]:
                return trial
        raise ExperimentError("進行中の試行が見つかりません。")

    @staticmethod
    def _stimulus_view(session, current):
        return {"ok": True, "kind": "practice" if current["kind"] == "practice" else "stimulus", "phase": current["phase"],
                "visual": copy.deepcopy(current["visual"]), "baseline": copy.deepcopy(session["configuration"]["neutral"]), "attempt": current["attempt"],
                "progress": {"completed": session["next_index"], "total": len(session["trials"])}}

    def event(self, session_id, token, name, actuate=None):
        if name not in EVENT_ORDER:
            raise ExperimentError("試行イベントが不正です。")
        with self.lock:
            session = self._session(session_id, token)
            current = session.get("current_trial")
            if not current or session["status"] != "running":
                raise ExperimentError("進行中の試行がありません。")
            expected = current["phase_index"]
            if expected >= len(EVENT_ORDER) or EVENT_ORDER[expected] != name:
                if name in [event["event"] for event in current["events"]]:
                    return {"ok": True, "duplicate": True, "phase": current["phase"]}
                raise ExperimentError("試行イベントの順序が不正です。")
            event = {"event": name, "attempt": current["attempt"], "at": self.wall_clock(),
                     "monotonic_s": round(self.monotonic() - current["started_monotonic"], 6)}
            if session.get("hardware_mode", "mock") == "hardware":
                if actuate is None:
                    raise ExperimentError("実機制御が利用できません。")
                event["device_result"] = actuate(session, current, name)
            else:
                event["device_result"] = {"mode": "mock", "status": "not_sent"}
            current["events"].append(event)
            current["phase_index"] += 1
            current["phase"] = EVENT_ORDER[current["phase_index"]] if current["phase_index"] < len(EVENT_ORDER) else "answer"
            trial = self._trial_for_current(session, current)
            if name == "transition":
                result = event.get("device_result", {})
                trial["target_command_at"] = result.get("command_requested_at", event["at"])
                trial["target_command_accepted_at"] = result.get("command_accepted_at")
            elif name == "target":
                trial["target_hold_started_at"] = event["at"]
            trial.setdefault("attempts", []).append(copy.deepcopy(event))
            field_by_event = {
                "instruction": "instruction_shown_at", "baseline": "baseline_at", "transition": "transition_started_at",
                "target": "target_hold_started_at", "return": "return_command_at", "response": "response_screen_at",
            }
            trial[field_by_event[name]] = event["at"]
            trial["timing_events"] = copy.deepcopy(current["events"])
            self._save()
            return {"ok": True, "phase": current["phase"], "event": event}

    def answer(self, session_id, token, perceived_emotion, confidence, latency_ms, free_text=None):
        if perceived_emotion not in EMOTION_NAMES:
            raise ExperimentError("感情回答を選択してください。")
        if perceived_emotion == "other":
            if confidence is not None:
                raise ExperimentError("その他・わからないの確信度は null にしてください。")
        elif isinstance(confidence, bool) or not isinstance(confidence, int) or confidence not in (1, 2, 3, 4):
            raise ExperimentError("確信度を1〜4で選択してください。")
        latency_ms = _number(latency_ms, "回答時間", 0, 86_400_000)
        if free_text is not None and (not isinstance(free_text, str) or len(free_text) > 2000):
            raise ExperimentError("自由記述は2000文字以内で入力してください。")
        with self.lock:
            session = self._session(session_id, token)
            current = session.get("current_trial")
            if not current or current["phase"] != "answer":
                raise ExperimentError("提示を完了してから回答してください。")
            if current["kind"] == "practice":
                session["practice_response"] = {"perceived_emotion": perceived_emotion, "confidence": confidence,
                                                "response_latency_ms": latency_ms, "answered_at": self.wall_clock(), "excluded_from_analysis": True}
            else:
                trial = self._trial_for_current(session, current)
                attempt_events = list(current["events"])
                timestamps = {event["event"]: event["at"] for event in attempt_events}
                trial.update({
                    "status": "answered", "response": {
                        "emotion_response_at": self.wall_clock(), "perceived_emotion": perceived_emotion,
                        "confidence": confidence, "response_latency_ms": round(latency_ms),
                    },
                    "instruction_shown_at": timestamps.get("instruction"), "baseline_at": timestamps.get("baseline"),
                    "transition_started_at": timestamps.get("transition"),
                    "target_command_at": trial.get("target_command_at", timestamps.get("transition")),
                    "target_command_accepted_at": trial.get("target_command_accepted_at"),
                    "target_hold_started_at": trial.get("target_hold_started_at", timestamps.get("target")),
                    "return_command_at": timestamps.get("return"), "response_screen_at": timestamps.get("response"),
                    "timing_events": attempt_events,
                    "error": "; ".join(item["message"] for item in trial.get("errors", [])) or None,
                    "device_result": {"mode": session.get("hardware_mode", "mock"),
                                       "status": "commands_accepted" if session.get("hardware_mode") == "hardware" else "simulated",
                                       "position_feedback": False}, "replayed": current["attempt"] > 1,
                    "attempt_count": current["attempt"],
                })
                session["next_index"] += 1
            if free_text:
                session["free_text"] = free_text
            session["current_trial"] = None
            if session["practice_response"] is not None and session["next_index"] >= len(session["trials"]):
                session["status"] = "completed"
                session["finished_at"] = self.wall_clock()
            self._save()
            return self.participant_view(session_id, token)

    def confirm_break(self, session_id, token):
        with self.lock:
            session = self._session(session_id, token)
            if session["next_index"] not in (14, 28):
                raise ExperimentError("現在は休憩タイミングではありません。")
            if session["next_index"] not in session["breaks_confirmed"]:
                session["breaks_confirmed"].append(session["next_index"])
                session["events"].append({"event": "break_resumed", "after_trial": session["next_index"], "at": self.wall_clock()})
                self._save()
            return self.participant_view(session_id, token)

    def abort(self, session_id, token, reason="participant_exit"):
        with self.lock:
            session = self._session(session_id, token)
            if session["status"] == "completed":
                raise ExperimentError("完了したセッションは中断できません。")
            if not isinstance(reason, str) or len(reason) > 120:
                raise ExperimentError("中断理由が不正です。")
            if session.get("current_trial") and session["current_trial"]["kind"] != "practice":
                trial = self._trial_for_current(session, session["current_trial"])
                trial["status"] = "invalid"
                trial["error"] = reason
            session["current_trial"] = None
            session["status"] = "aborted"
            session["events"].append({"event": "session_aborted", "reason": reason, "at": self.wall_clock()})
            self._save()
            return self.participant_view(session_id, token)

    def record_error(self, session_id, token, message):
        if not isinstance(message, str) or len(message) > 240:
            raise ExperimentError("エラー内容が長すぎます。")
        with self.lock:
            session = self._session(session_id, token)
            current = session.get("current_trial")
            entry = {"message": message, "at": self.wall_clock()}
            if current:
                trial = self._trial_for_current(session, current)
                trial.setdefault("errors", []).append(entry)
            else:
                session["events"].append({"event": "ui_error", **entry})
            self._save()
            return {"ok": True}

    def add_free_text(self, session_id, token, text):
        if not isinstance(text, str) or len(text) > 2000:
            raise ExperimentError("自由記述は2000文字以内で入力してください。")
        with self.lock:
            session = self._session(session_id, token)
            if session["status"] != "completed":
                raise ExperimentError("自由記述は終了後に保存できます。")
            session["free_text"] = text
            self._save()
            return {"ok": True}

    def csv_export(self, session_id):
        with self.lock:
            session = self._session(session_id)
            output = io.StringIO(newline="")
            fields = ["session_id", "participant_id", "mode", "hardware_mode", "consent_at", "configuration_version", "configuration_hash", "random_seed", "presentation_index", "trial_slot_id", "intended_emotion", "factors", "physical_state_id", "target_values", "baseline_values", "instruction_shown_at", "baseline_at", "transition_started_at", "target_command_at", "target_command_accepted_at", "target_hold_started_at", "return_command_at", "response_screen_at", "emotion_response_at", "perceived_emotion", "confidence", "response_latency_ms", "device_result", "timing_events", "replayed", "attempt_count", "status", "error", "is_practice"]
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            for trial in session["trials"]:
                response = trial.get("response") or {}
                row = {
                    "session_id": session["session_id"], "participant_id": session["participant_id"], "mode": session["mode"],
                    "hardware_mode": session["hardware_mode"], "consent_at": session["consent_at"],
                    "configuration_version": session["configuration_version"], "configuration_hash": session["configuration_hash"],
                    "random_seed": session["random_seed"], "presentation_index": trial["presentation_index"],
                    "trial_slot_id": trial["trial_slot_id"], "intended_emotion": trial["intended_emotion"],
                    "factors": "".join(trial["factors"]), "physical_state_id": trial["physical_state_id"],
                    "target_values": _stable_json(trial["actual_target_values"]), "baseline_values": _stable_json(session["configuration"]["neutral"]),
                    "perceived_emotion": response.get("perceived_emotion"), "confidence": response.get("confidence"),
                    "emotion_response_at": response.get("emotion_response_at"), "response_latency_ms": response.get("response_latency_ms"),
                    "device_result": _stable_json(trial.get("device_result")) if trial.get("device_result") else "",
                    "timing_events": _stable_json(trial.get("timing_events", [])),
                    "replayed": trial.get("replayed", False), "attempt_count": trial.get("attempt_count", 0),
                    "status": trial["status"], "error": trial.get("error"), "is_practice": False,
                }
                for key in ("instruction_shown_at", "baseline_at", "transition_started_at", "target_command_at",
                            "target_command_accepted_at", "target_hold_started_at", "return_command_at", "response_screen_at"):
                    row[key] = trial.get(key)
                writer.writerow(row)
            return output.getvalue()

    def json_export(self, session_id):
        with self.lock:
            session = copy.deepcopy(self._session(session_id))
            session.pop("_participant_token", None)
            return {"schema_version": 1, **session}

