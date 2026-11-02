#!/usr/bin/env python3
"""Serve the legacy eye UI through the existing, sole servo controller."""

import argparse
import copy
import json
import math
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from live_control import LiveController, LiveControlError
from experiment_engine import DEFAULT_CONFIG, SPEC_SNAPSHOT, ExperimentError, ExperimentStore, generate_trials, utc_now, validate_config

ROOT = Path(__file__).resolve().parent
EYE_IDS = frozenset(range(6))
ALLOWED_POSTS = frozenset({"/api/nudge", "/api/center", "/api/release", "/api/record", "/api/move_saved"})
SERVO_API_BASE = os.environ.get("ARIA_SERVO_API_BASE", "http://127.0.0.1:8768").rstrip("/")


class Rejected(ValueError):
    pass


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Rejected(f"{label} は有限の数値で指定してください。")
    return value


class Controller:
    def __init__(self, limits, upstream=None):
        self.limits = limits
        self.upstream = upstream or self.request_upstream
        self.lock = threading.RLock()
        self.live = None

    @staticmethod
    def request_upstream(path, body=None):
        raw = None if body is None else json.dumps(body).encode()
        request = Request(SERVO_API_BASE + path, data=raw,
                          headers={"Content-Type": "application/json"} if raw is not None else {})
        try:
            with urlopen(request, timeout=50 if path == "/api/move_saved" else 3) as response:
                result = json.load(response)
        except HTTPError as error:
            raise Rejected(f"サーボ制御側でエラーが発生しました（HTTP {error.code}）。") from error
        if result.get("ok") is not True:
            raise Rejected("サーボ制御側が操作を受け付けませんでした。")
        return result

    def check_value(self, channel, value):
        value = number(value, "位置")
        bounds = self.limits[str(channel)]
        if not bounds["min"] <= value <= bounds["max"]:
            raise Rejected(f"CH{channel}: {value}µs は保存済み校正範囲 {bounds['min']}〜{bounds['max']}µs の外です。")

    @staticmethod
    def eye_channel(value):
        if isinstance(value, bool) or not isinstance(value, int) or value not in EYE_IDS:
            raise Rejected("操作対象は眼球・まぶたのCH0〜5です。")
        return value

    def public_state(self, state):
        result = copy.deepcopy(state)
        result["channels"] = [channel for channel in result["channels"] if channel["id"] in EYE_IDS]
        for channel in result["channels"]:
            channel.update({key: self.limits[str(channel["id"])][key] for key in ("min", "max")})
        for key in ("values", "active"):
            result[key] = {k: v for k, v in result.get(key, {}).items() if k in self.limits}
        for pose in result.get("saved_positions", []):
            pose["values"] = {k: v for k, v in pose["values"].items() if k in self.limits}
        result["limit_source"] = "2026-08-16 named calibration poses and current SYNC constants"
        return result

    def validate_pose(self, pose, current):
        for channel in EYE_IDS:
            self.check_value(channel, pose["values"][str(channel)])
        for channel, value in current["values"].items():
            if int(channel) not in EYE_IDS and pose["values"].get(channel) != value:
                raise Rejected("この保存姿勢は現在の眉位置も変更します。眼球のみの画面では再生できません。")

    def execute(self, path, body):
        if path not in ALLOWED_POSTS:
            raise Rejected("対応していない操作です。")
        if not isinstance(body, dict):
            raise Rejected("JSONオブジェクトが必要です。")
        with self.lock:
            if self.live and self.live.token:
                if path != "/api/release":
                    raise Rejected("かんたん操作を終了してから調整してください。")
                self.live.token = None
                self.live.pose_active = False
                self.live.frame = None
            state = self.upstream("/api/state")
            if state.get("sync", {}).get("active") and path != "/api/release":
                raise Rejected("別の同期制御が動作中です。先にその再生を停止してください。")
            if path == "/api/nudge":
                channel = self.eye_channel(body.get("channel"))
                delta = number(body.get("delta"), "移動量")
                if abs(delta) > 200:
                    raise Rejected("1操作の移動量は200µs以内です。")
                bounds = self.limits[str(channel)]
                current = state["values"][str(channel)]
                self.check_value(channel, current)
                target = int(max(bounds["min"], min(bounds["max"], current + delta)))
                if target != current:
                    self.upstream("/api/set", {"channel": channel, "us": target})
            elif path in {"/api/center", "/api/release"}:
                channels = list(range(6)) if body.get("channel") is None else [self.eye_channel(body["channel"])]
                if path == "/api/center":
                    for channel in channels:
                        config = next(c for c in state["channels"] if c["id"] == channel)
                        self.check_value(channel, config["home"])
                for channel in channels:
                    self.upstream(path, {"channel": channel})
            elif path == "/api/record":
                label = body.get("label", "")
                if not isinstance(label, str) or len(label) > 200:
                    raise Rejected("記録メモは200文字以内の文字列で指定してください。")
                return self.upstream(path, {"label": label})
            else:
                duration = number(body.get("duration", 0.7), "移動時間")
                if not 0.05 <= duration <= 20:
                    raise Rejected("移動時間は0.05〜20秒です。")
                poses = {str(p["id"]): p for p in state.get("saved_positions", [])}
                to_id, from_id = str(body.get("to_id")), str(body.get("from_id", "current"))
                requested = [to_id] + ([] if from_id == "current" else [from_id])
                for channel in EYE_IDS:
                    self.check_value(channel, state["values"][str(channel)])
                for pose_id in requested:
                    if pose_id not in poses:
                        raise Rejected("保存姿勢が見つかりません。画面を更新してください。")
                    self.validate_pose(poses[pose_id], state)
                prior_brows_released = [int(k) for k, active in state.get("active", {}).items()
                                        if int(k) not in EYE_IDS and not active]
                try:
                    self.upstream(path, {"from_id": from_id, "to_id": to_id, "duration": duration})
                finally:
                    for channel in prior_brows_released:
                        self.upstream("/api/release", {"channel": channel})
            return self.public_state(self.upstream("/api/state"))


class ExperimentHardware:
    """Send explicitly enabled experiment poses through the existing live controller."""

    def __init__(self, controller):
        self.controller = controller
        self.lock = threading.RLock()
        self.session_id = None
        self.token = None
        self.sequence = 0
        self.neutral = None

    @staticmethod
    def payload(values):
        return {
            "pose": {
                "x": max(-1.0, min(1.0, float(values["gaze_horizontal_deg"]) / 20.0)),
                "y": max(-1.0, min(1.0, float(values["gaze_vertical_deg"]) / 20.0)),
                # The saved UI value runs opposite to physical openness: 0 is open, 100 is closed.
                "openness": 1.0 - float(values["eyelid_open_percent"]) / 100.0,
            },
            "irisColor": values["iris_hex"],
        }

    def status(self):
        try:
            state = self.controller.live.status()
        except Exception as error:
            raise LiveControlError("Piのサーボまたは虹彩表示サービスに接続できません。") from error
        return {"ok": True, "connected": True, "active": state["active"],
                "pose": state["pose"], "irisColor": state["irisColor"]}

    def preflight(self):
        state = self.status()
        if state["active"]:
            raise LiveControlError("手動調整が動作中です。手動調整を終了してから実験を作成してください。")
        return state

    def _acquire(self, session_id):
        live = self.controller.live
        if self.session_id not in (None, session_id) and self.token and live.token == self.token:
            raise LiveControlError("別の実験セッションが実機を操作中です。")
        if self.token and live.token == self.token and self.session_id == session_id:
            return
        self.session_id = None
        self.token = None
        if live.token:
            raise LiveControlError("手動調整が実機を操作中です。先に終了してください。")
        self.token = live.start()["token"]
        self.session_id = session_id
        self.sequence = 0

    def actuate(self, session, current, name):
        values = None
        if name == "baseline":
            values = session["configuration"]["neutral"]
        elif name == "transition":
            values = current["visual"]
        elif name == "return":
            values = session["configuration"]["neutral"]
        if values is None:
            return {"mode": "hardware", "status": "no_command", "event": name}
        session_id = session["session_id"]
        with self.lock:
            self.neutral = copy.deepcopy(session["configuration"]["neutral"])
            self._acquire(session_id)
            requested_at = utc_now()
            self.sequence += 1
            result = self.controller.live.input({"token": self.token, "sequence": self.sequence,
                                                 **self.payload(values)})
            return {"mode": "hardware", "status": "accepted", "event": name,
                    "command_requested_at": requested_at, "command_accepted_at": utc_now(),
                    "target_values": copy.deepcopy(values), "controller_sequence": result.get("sequence"),
                    "position_feedback": False}

    def keepalive(self, session_id):
        with self.lock:
            if self.session_id != session_id or not self.token or self.controller.live.token != self.token:
                return {"ok": True, "active": False}
            self.controller.live.keepalive({"token": self.token})
            return {"ok": True, "active": True}

    def stop(self, session_id):
        with self.lock:
            if self.session_id != session_id or not self.token:
                return {"ok": True}
            token = self.token
            try:
                if self.controller.live.token == token:
                    if self.neutral:
                        self.sequence += 1
                        self.controller.live.input({"token": token, "sequence": self.sequence,
                                                    **self.payload(self.neutral)})
                    self.controller.live.stop({"token": token})
            finally:
                self.session_id = None
                self.token = None
                self.sequence = 0
                self.neutral = None
            return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    controller = None
    hardware = None
    experiments = None
    admin_token = None

    def send(self, status, data, content_type="application/json; charset=utf-8", extra_headers=None):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if self._admin_required(path, query) and not self._check_admin():
                return
            if path in {"/", "/index.html"}:
                self.send(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/study":
                self.send(200, (ROOT / "study.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/experiment/admin":
                self.send(200, (ROOT / "experiment-admin.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/experiment/participant":
                self.send(200, (ROOT / "experiment-participant.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/simple":
                self.send(200, (ROOT / "simple.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/simple.js":
                self.send(200, (ROOT / "simple.js").read_bytes(), "text/javascript; charset=utf-8")
            elif path == "/simple.css":
                self.send(200, (ROOT / "simple.css").read_bytes(), "text/css; charset=utf-8")
            elif path == "/experiment.css":
                self.send(200, (ROOT / "experiment.css").read_bytes(), "text/css; charset=utf-8")
            elif path == "/api/live/status":
                self.send(200, self.controller.live.status())
            elif path == "/api/state":
                self.send(200, self.controller.public_state(self.controller.upstream(path)))
            elif path == "/api/experiment/hardware/status":
                self.send(200, self.hardware.preflight())
            elif path == "/api/experiment/config":
                self.send(200, {"ok": True, **self.experiments.get_config_state()})
            elif path == "/api/experiment/default-config":
                self.send(200, {"ok": True, **self.experiments.get_config_state(),
                                "spec_snapshot": SPEC_SNAPSHOT, "source": "server_saved_configuration"})
            elif path == "/api/experiment/preview":
                self.send(405, {"ok": False, "error": "POSTで設定を送信してください。"})
            elif path == "/api/experiment/sessions":
                self.send(200, {"ok": True, "sessions": self.experiments.list_sessions()})
            elif path == "/api/experiment/session":
                session_id = query.get("session", [""])[0]
                if query.get("view", ["participant"])[0] == "admin":
                    session = self.experiments._session(session_id)
                    self.send(200, {"ok": True, "session": self.experiments.admin_view(session)})
                else:
                    self.send(404, {"ok": False, "error": "Not found"})
            elif path.startswith("/api/experiment/export/"):
                export_name = path.rsplit("/", 1)[-1]
                if export_name.endswith(".csv"):
                    session_id = export_name[:-4]
                    data = self.experiments.csv_export(session_id).encode("utf-8-sig")
                    self.send(200, data, "text/csv; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="{session_id}.csv"'})
                elif export_name.endswith(".json"):
                    session_id = export_name[:-5]
                    data = json.dumps(self.experiments.json_export(session_id), ensure_ascii=False, indent=2).encode("utf-8")
                    self.send(200, data, "application/json; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="{session_id}.json"'})
                else:
                    self.send(404, {"ok": False, "error": "Not found"})
            else:
                self.send(404, {"ok": False, "error": "Not found"})
        except ExperimentError as error:
            self.send(400, {"ok": False, "error": str(error)})
        except Exception as error:
            self.send(502, {"ok": False, "error": str(error)})

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            self.send(403, {"ok": False, "error": "Origin mismatch"})
            return
        try:
            path = urlsplit(self.path).path
            if self._admin_required(path, parse_qs(urlsplit(self.path).query)) and not self._check_admin():
                return
            if "application/json" not in self.headers.get("Content-Type", "").lower():
                raise Rejected("Content-Type: application/json が必要です。")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32768:
                raise Rejected("リクエストサイズが不正です。")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise Rejected("JSONオブジェクトが必要です。")
            routes = {"/api/live/start": lambda: self.controller.live.start(),
                      "/api/live/input": lambda: self.controller.live.input(body),
                      "/api/live/keepalive": lambda: self.controller.live.keepalive(body),
                      "/api/live/stop": lambda: self.controller.live.stop(body),
                      "/api/experiment/preview": lambda: {"ok": True, "trials": generate_trials(validate_config(body.get("config")), 0)},
                      "/api/experiment/config": lambda: self._update_experiment_config(body),
                      "/api/experiment/session/create": lambda: self._create_experiment_session(body),
                      "/api/experiment/session/state": lambda: self.experiments.participant_view(body.get("session_id"), body.get("access")),
                      "/api/experiment/consent": lambda: self.experiments.consent(body.get("session_id"), body.get("access"), body.get("accepted")),
                      "/api/experiment/start": lambda: self.experiments.start(body.get("session_id"), body.get("access")),
                      "/api/experiment/trial/start": lambda: self.experiments.begin_trial(body.get("session_id"), body.get("access"), body.get("replay", False)),
                      "/api/experiment/trial/event": lambda: self._experiment_event(body),
                      "/api/experiment/heartbeat": lambda: self._experiment_heartbeat(body),
                      "/api/experiment/hardware/stop": lambda: self._experiment_hardware_stop(body),
                      "/api/experiment/answer": lambda: self._experiment_answer(body),
                      "/api/experiment/break": lambda: self._experiment_break(body),
                      "/api/experiment/abort": lambda: self._experiment_abort(body),
                      "/api/experiment/error": lambda: self._experiment_error(body),
                      "/api/experiment/feedback": lambda: self.experiments.add_free_text(body.get("session_id"), body.get("access"), body.get("free_text"))}
            self.send(200, routes[path]() if path in routes else self.controller.execute(path, body))
        except (Rejected, LiveControlError, ExperimentError, ValueError, KeyError, TypeError) as error:
            self.send(400, {"ok": False, "error": str(error)})
        except Exception as error:
            self.send(502, {"ok": False, "error": str(error)})

    def _create_experiment_session(self, body):
        hardware_mode = body.get("hardware_mode", "mock")
        if hardware_mode == "hardware":
            self.hardware.preflight()
        session, token = self.experiments.create(body.get("participant_id"), body.get("mode"),
                                                 body.get("config"), body.get("random_seed"), hardware_mode)
        return {"ok": True, "session": session, "access": token}

    def _update_experiment_config(self, body):
        return {"ok": True, "config": self.experiments.update_current_config(body.get("config"))}

    def _experiment_event(self, body):
        return self.experiments.event(body.get("session_id"), body.get("access"),
                                      body.get("event"), self.hardware.actuate)

    def _participant_session(self, body):
        session_id, access = body.get("session_id"), body.get("access")
        with self.experiments.lock:
            session = self.experiments._session(session_id, access)
            return session_id, session.get("hardware_mode", "mock")

    def _experiment_heartbeat(self, body):
        session_id, mode = self._participant_session(body)
        if mode == "hardware":
            return self.hardware.keepalive(session_id)
        return {"ok": True, "active": False}

    def _experiment_hardware_stop(self, body):
        session_id, _ = self._participant_session(body)
        return self.hardware.stop(session_id)

    def _experiment_answer(self, body):
        view = self.experiments.answer(body.get("session_id"), body.get("access"),
                                       body.get("perceived_emotion"), body.get("confidence"),
                                       body.get("response_latency_ms"), body.get("free_text"))
        if view.get("status") == "completed":
            self.hardware.stop(body.get("session_id"))
        return view

    def _experiment_break(self, body):
        session_id, _ = self._participant_session(body)
        self.hardware.stop(session_id)
        return self.experiments.confirm_break(session_id, body.get("access"))

    def _experiment_abort(self, body):
        session_id, _ = self._participant_session(body)
        self.hardware.stop(session_id)
        return self.experiments.abort(session_id, body.get("access"))

    def _experiment_error(self, body):
        session_id, _ = self._participant_session(body)
        self.hardware.stop(session_id)
        return self.experiments.record_error(session_id, body.get("access"), body.get("message"))

    @staticmethod
    def _admin_required(path, query):
        if path in {"/api/experiment/config", "/api/experiment/default-config", "/api/experiment/hardware/status",
                    "/api/experiment/preview", "/api/experiment/sessions"}:
            return True
        if path == "/api/experiment/session":
            return True
        return path.startswith("/api/experiment/export/") or path == "/api/experiment/session/create"

    def _check_admin(self):
        if not self.admin_token:
            return True
        supplied = self.headers.get("X-Aria-Admin-Token", "")
        if not supplied:
            self.send(401, {"ok": False, "error": "Piに管理キーが設定されています。管理画面に入力してください。"})
            return False
        if not secrets.compare_digest(supplied, self.admin_token):
            self.send(403, {"ok": False, "error": "管理者トークンが正しくありません。"})
            return False
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    limits = json.loads((ROOT / "limits.json").read_text())["channels"]
    Handler.controller = Controller(limits)
    Handler.controller.live = LiveController(Handler.controller)
    Handler.hardware = ExperimentHardware(Handler.controller)
    Handler.experiments = ExperimentStore()
    Handler.admin_token = os.environ.get("ARIA_EXPERIMENT_ADMIN_TOKEN")
    Handler.controller.live.watch()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
