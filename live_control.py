"""Leased, calibrated control of both eyes and their shared iris color."""

import copy
import json
import math
import os
import re
import secrets
import threading
import time
from urllib.request import Request, urlopen

LCD_API_BASE = os.environ.get("ARIA_LCD_API_BASE", "http://127.0.0.1:8769").rstrip("/")


class LiveControlError(ValueError):
    pass


def bounded(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise LiveControlError(f"{name} は有限の数値が必要です。")
    if not low <= value <= high:
        raise LiveControlError(f"{name} は{low}〜{high}の範囲です。")
    return float(value)


class LiveController:
    LEASE_SECONDS = 1.4
    # Horizontal endpoints follow the user's front-view calibration photographs.
    GAZE_X_DIRECTION = 1
    GAZE_Y_DIRECTION = 1

    def __init__(self, controller, lcd=None, now=time.monotonic):
        self.controller = controller
        self.lcd = lcd or self.request_lcd
        self.now = now
        self.token = None
        self.sequence = -1
        self.last_seen = 0
        self.pose_active = False
        self.frame = None
        self.last_error = None

    @staticmethod
    def request_lcd(body=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(LCD_API_BASE + "/api/state", data=data,
                          headers={"Content-Type": "application/json"} if data else {})
        with urlopen(request, timeout=3) as response:
            result = json.load(response)
        if not result.get("ok"):
            raise LiveControlError("目の色を更新できませんでした。")
        return result

    def _end(self, release=False):
        had_pose = self.pose_active
        self.token = None
        self.pose_active = False
        self.frame = None
        if release:
            for channel in range(6):
                self.controller.upstream("/api/release", {"channel": channel})
        elif had_pose:
            self.controller.upstream("/api/sync", {"gaze": {"x": 0, "y": 0}, "blink": 0, "eyeOnly": True})

    def expire(self):
        with self.controller.lock:
            if self.token and self.now() - self.last_seen > self.LEASE_SECONDS:
                try:
                    self._end()
                except Exception as error:
                    self.last_error = str(error)

    def watch(self):
        def loop():
            while True:
                self.expire()
                time.sleep(0.1)
        threading.Thread(target=loop, name="aria-simple-control-lease", daemon=True).start()

    def _owner(self, body):
        self.expire()
        if not self.token or body.get("token") != self.token:
            raise LiveControlError("操作権が切れました。もう一度操作してください。")

    def state_pose(self, state):
        configs = {c["id"]: c for c in state["channels"]}
        limits = self.controller.limits
        values = state["values"]
        def inverse(channel, negative_key, positive_key):
            center = configs[channel]["home"]
            current = values[str(channel)]
            endpoints = limits[str(channel)]["endpoints"]
            negative, positive = endpoints[negative_key], endpoints[positive_key]
            delta = current - center
            if delta * (negative - center) > 0:
                return -min(1.0, delta / (negative - center))
            if delta * (positive - center) > 0:
                return min(1.0, delta / (positive - center))
            return 0.0
        x = sum(inverse(c, "viewer_left", "viewer_right") for c in (1, 2)) / 2
        y = sum(inverse(c, "down", "up") for c in (0, 3)) / 2
        closed = []
        for c in (4, 5):
            e = limits[str(c)]["endpoints"]
            closed.append(max(0, min(1, (values[str(c)] - e["open_us"]) / (e["closed_us"] - e["open_us"]))))
        return {"x": x * self.GAZE_X_DIRECTION, "y": y * self.GAZE_Y_DIRECTION,
                "openness": 1 - sum(closed) / 2}

    def status(self):
        with self.controller.lock:
            self.expire()
            servo = self.controller.upstream("/api/state")
            color = self.lcd()
            return {"ok": True, "active": self.token is not None,
                    "pose": self.state_pose(servo), "irisColor": color.get("irisColor"),
                    "defaultColor": "#EAA636", "error": self.last_error}

    def start(self):
        with self.controller.lock:
            self.expire()
            if self.token:
                raise LiveControlError("別の画面で操作中です。そちらの操作を終了してください。")
            servo = self.controller.upstream("/api/state")
            sync = servo.get("sync", {})
            if sync.get("active") and not sync.get("eye_only"):
                raise LiveControlError("別のモーションを再生中です。先に停止してください。")
            self.token = secrets.token_urlsafe(24)
            self.sequence = -1
            self.pose_active = False
            self.last_seen = self.now()
            self.last_error = None
            return {"ok": True, "token": self.token}

    def input(self, body):
        with self.controller.lock:
            self._owner(body)
            sequence = body.get("sequence")
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
                raise LiveControlError("操作の順序番号が不正です。")
            if sequence <= self.sequence:
                return {"ok": True, "ignored": True}
            frame = None
            if "pose" in body:
                pose = body["pose"]
                if not isinstance(pose, dict):
                    raise LiveControlError("位置の形式が不正です。")
                x = bounded(pose.get("x"), -1, 1, "左右")
                y = bounded(pose.get("y"), -1, 1, "上下")
                openness = bounded(pose.get("openness"), 0, 1, "まぶた")
                frame = {"gaze": {"x": x * self.GAZE_X_DIRECTION, "y": y * self.GAZE_Y_DIRECTION},
                         "blink": 1 - openness, "eyeOnly": True}
            if "irisColor" in body:
                color = body["irisColor"]
                if color is not None and (not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color)):
                    raise LiveControlError("色は#RRGGBB形式で指定してください。")
            if frame is None and "irisColor" not in body:
                raise LiveControlError("変更する項目がありません。")
            self.sequence = sequence
            self.last_seen = self.now()
            try:
                if frame is not None:
                    self.frame = copy.deepcopy(frame)
                    self.pose_active = True
                    self.controller.upstream("/api/sync", frame)
                if "irisColor" in body:
                    self.lcd({"irisColor": body["irisColor"], "mode": "manual"})
            except Exception as error:
                self.last_error = str(error)
                try:
                    self._end()
                except Exception:
                    pass
                raise
            self.last_seen = self.now()
            return {"ok": True, "sequence": sequence}

    def keepalive(self, body):
        with self.controller.lock:
            self._owner(body)
            self.last_seen = self.now()
            if self.pose_active:
                self.controller.upstream("/api/sync", self.frame)
            self.last_seen = self.now()
            return {"ok": True}

    def stop(self, body):
        with self.controller.lock:
            if body.get("release") is True:
                self._end(release=True)
                return {"ok": True}
            if not self.token:
                return {"ok": True}
            self._owner(body)
            self._end()
            return {"ok": True}
