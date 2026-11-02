import copy
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from legacy_ui_server import Controller, Rejected
from live_control import LiveController, LiveControlError, resolve_ipv4_base_url

LIMITS = json.loads((Path(__file__).resolve().parents[1] / "limits.json").read_text())["channels"]
HOMES = [1600, 1450, 1600, 1550, 850, 2200, 1450, 1450, 1500, 1500]


class FakeHardware:
    def __init__(self):
        self.writes = []
        self.lcd_writes = []
        self.clock = 10.0
        self.state = {"ok": True, "values": {str(i): v for i, v in enumerate(HOMES)},
                      "channels": [{"id": i, "name": f"CH{i}", "home": v, "min": 250, "max": 2600} for i, v in enumerate(HOMES)],
                      "active": {str(i): i < 6 for i in range(10)}, "sync": {"active": False, "eye_only": False}, "saved_positions": []}
        self.color = {"ok": True, "irisColor": None}

    def servo(self, path, body=None):
        if body is not None:
            self.writes.append((path, copy.deepcopy(body)))
            if path == "/api/sync":
                self.state["sync"] = {"active": True, "eye_only": body.get("eyeOnly", False)}
            if path == "/api/release":
                self.state["active"][str(body["channel"])] = False
                self.state["sync"]["active"] = False
        return copy.deepcopy(self.state)

    def lcd(self, body=None):
        if body is not None:
            self.lcd_writes.append(copy.deepcopy(body))
            self.color.update(body)
        return copy.deepcopy(self.color)


class LiveControlTests(unittest.TestCase):
    def setUp(self):
        self.hardware = FakeHardware()
        self.controller = Controller(LIMITS, self.hardware.servo)
        self.live = LiveController(self.controller, self.hardware.lcd, lambda: self.hardware.clock)
        self.controller.live = self.live

    def start(self):
        return self.live.start()["token"]

    def test_api_base_resolves_hostname_to_ipv4_and_falls_back_if_unavailable(self):
        address = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.0.2.8", 8768))
        with patch("live_control.socket.getaddrinfo", return_value=[address]):
            self.assertEqual(resolve_ipv4_base_url("http://raspberrypi.local:8768"), "http://192.0.2.8:8768")
        with patch("live_control.socket.getaddrinfo", side_effect=OSError("not resolved")):
            self.assertEqual(resolve_ipv4_base_url("http://raspberrypi.local:8768"), "http://raspberrypi.local:8768")

    def test_status_and_start_do_not_move_hardware(self):
        self.assertEqual(self.live.status()["pose"], {"x": 0, "y": 0, "openness": 1})
        self.start()
        self.assertEqual(self.hardware.writes, [])
        self.assertEqual(self.hardware.lcd_writes, [])

    def test_normalized_pose_uses_only_eyes(self):
        self.live.input({"token": self.start(), "sequence": 1, "pose": {"x": -1, "y": 1, "openness": 0.25}})
        self.assertEqual(self.hardware.writes[-1], ("/api/sync", {"gaze": {"x": -1, "y": -1}, "blink": 0.75, "eyeOnly": True}))
        self.assertEqual(self.hardware.lcd_writes, [])

    def test_screen_directions_follow_horizontal_calibration_and_vertical_mapping(self):
        token = self.start()
        directions = [(-1, 0, -1, 0), (1, 0, 1, 0), (0, 1, 0, -1), (0, -1, 0, 1)]
        for sequence, (x, y, expected_x, expected_y) in enumerate(directions, 1):
            with self.subTest(x=x, y=y):
                self.live.input({"token": token, "sequence": sequence, "pose": {"x": x, "y": y, "openness": 0.6}})
                frame = self.hardware.writes[-1][1]
                self.assertEqual(frame["gaze"], {"x": expected_x, "y": expected_y})
                self.assertAlmostEqual(frame["blink"], 0.4)

    def test_reported_hardware_pose_maps_back_to_screen_direction(self):
        self.hardware.state["values"].update({"0": 1700, "1": 950, "2": 1100, "3": 1450})
        self.assertEqual(self.live.status()["pose"], {"x": -1, "y": 1, "openness": 1})

    def test_ui_left_and_right_produce_the_exact_user_photographed_endpoints(self):
        from test_servo_eye_only import servo
        token = self.start()
        for sequence, (x, expected) in enumerate([(-1, (950, 1100)), (1, (2200, 2000))], 1):
            with self.subTest(screen_x=x):
                self.live.input({"token": token, "sequence": sequence, "pose": {"x": x, "y": 0, "openness": 1}})
                command = self.hardware.writes[-1][1]
                frame = servo.ServoRig._target_for_pose(None, {**command["gaze"], "blink": command["blink"]}, eye_only=True)
                self.assertEqual((frame[1], frame[2]), expected)
                self.assertEqual((frame[0], frame[3]), (1600, 1550))
                self.hardware.state["values"].update({str(k): v for k, v in frame.items()})
                self.assertEqual(self.live.state_pose(self.hardware.state)["x"], x)

    def test_eyelid_open_close_and_midpoint_match_installed_direction(self):
        from test_servo_eye_only import servo
        token = self.start()
        for sequence, (openness, expected) in enumerate([(0, (2000, 1000)), (0.5, (1425, 1600)), (1, (850, 2200))], 1):
            with self.subTest(openness=openness):
                self.live.input({"token": token, "sequence": sequence, "pose": {"x": 0, "y": 0, "openness": openness}})
                command = self.hardware.writes[-1][1]
                frame = servo.ServoRig._target_for_pose(None, {**command["gaze"], "blink": command["blink"]}, eye_only=True)
                self.assertEqual((frame[4], frame[5]), expected)
                self.assertEqual((frame[0], frame[1], frame[2], frame[3]), tuple(HOMES[:4]))
                self.hardware.state["values"].update({str(k): v for k, v in frame.items()})
                self.assertAlmostEqual(self.live.state_pose(self.hardware.state)["openness"], openness)

    def test_stop_returns_to_centered_open_pose(self):
        from test_servo_eye_only import servo
        token = self.start()
        self.live.input({"token": token, "sequence": 1, "pose": {"x": 0, "y": 0, "openness": 1}})
        self.live.stop({"token": token})
        command = self.hardware.writes[-1][1]
        frame = servo.ServoRig._target_for_pose(None, {**command["gaze"], "blink": command["blink"]}, eye_only=True)
        self.assertEqual((frame[4], frame[5]), (850, 2200))

    def test_color_does_not_send_servo_commands_or_change_pupil_size(self):
        token = self.start()
        self.live.input({"token": token, "sequence": 1, "irisColor": "#123ABC"})
        self.live.keepalive({"token": token})
        self.live.stop({"token": token})
        self.assertEqual(self.hardware.writes, [])
        self.assertEqual(self.hardware.lcd_writes, [{"irisColor": "#123ABC", "mode": "manual"}])

    def test_invalid_color_prevents_even_a_valid_pose_write(self):
        with self.assertRaises(LiveControlError):
            self.live.input({"token": self.start(), "sequence": 1, "pose": {"x": 0, "y": 0, "openness": 0}, "irisColor": "invalid"})
        self.assertEqual(self.hardware.writes, [])

    def test_invalid_pose_prevents_hardware_write(self):
        token = self.start()
        for pose in [{"x": 1.1, "y": 0, "openness": 1}, {"x": float("nan"), "y": 0, "openness": 1}, {"x": 0, "y": 0, "openness": -1}, {"x": True, "y": 0, "openness": 1}]:
            with self.assertRaises(LiveControlError):
                self.live.input({"token": token, "sequence": 1, "pose": pose})
        self.assertEqual(self.hardware.writes, [])

    def test_out_of_order_frames_are_ignored(self):
        token = self.start()
        self.live.input({"token": token, "sequence": 2, "pose": {"x": 1, "y": 0, "openness": 1}})
        result = self.live.input({"token": token, "sequence": 1, "pose": {"x": -1, "y": 0, "openness": 1}})
        self.assertTrue(result["ignored"])
        self.assertEqual(len(self.hardware.writes), 1)

    def test_stop_revokes_queued_frames_and_centers_without_recoloring(self):
        token = self.start()
        self.live.input({"token": token, "sequence": 1, "pose": {"x": 1, "y": 1, "openness": 0}})
        self.live.stop({"token": token})
        self.assertEqual(self.hardware.writes[-1][1], {"gaze": {"x": 0, "y": 0}, "blink": 0, "eyeOnly": True})
        count = len(self.hardware.writes)
        with self.assertRaises(LiveControlError):
            self.live.input({"token": token, "sequence": 2, "pose": {"x": 1, "y": 0, "openness": 0}})
        self.assertEqual(len(self.hardware.writes), count)

    def test_release_changes_only_six_eye_outputs(self):
        self.live.stop({"token": self.start(), "release": True})
        self.assertEqual(self.hardware.writes, [("/api/release", {"channel": i}) for i in range(6)])

    def test_release_works_without_an_active_lease(self):
        self.live.stop({"token": None, "release": True})
        self.assertEqual(self.hardware.writes, [("/api/release", {"channel": i}) for i in range(6)])

    def test_abandoned_lease_returns_to_neutral(self):
        token = self.start()
        self.live.input({"token": token, "sequence": 1, "pose": {"x": 1, "y": 0, "openness": 0}})
        self.hardware.clock += 1.5
        self.live.expire()
        self.assertIsNone(self.live.token)
        self.assertEqual(self.hardware.writes[-1][1]["blink"], 0)

    def test_second_client_and_legacy_writes_cannot_take_over(self):
        self.start()
        with self.assertRaises(LiveControlError): self.start()
        with self.assertRaises(Rejected): self.controller.execute("/api/center", {})
        self.assertEqual(self.hardware.writes, [])

    def test_legacy_release_revokes_live_session(self):
        token = self.start()
        self.controller.execute("/api/release", {})
        with self.assertRaises(LiveControlError): self.live.keepalive({"token": token})

    def test_existing_timeline_blocks_start(self):
        self.hardware.state["sync"] = {"active": True, "eye_only": False}
        with self.assertRaises(LiveControlError): self.start()

    def test_position_is_read_from_values_not_stale_sync_metadata(self):
        self.hardware.state["sync"]["pose"] = {"x": 1, "y": -1, "blink": 1}
        self.assertEqual(self.live.status()["pose"], {"x": 0, "y": 0, "openness": 1})
        self.hardware.state["values"].update({"0": 1200, "1": 950, "2": 1100, "3": 1950, "4": 2000, "5": 1000})
        self.assertEqual(self.live.status()["pose"], {"x": -1, "y": -1, "openness": 0})


if __name__ == "__main__":
    unittest.main()
