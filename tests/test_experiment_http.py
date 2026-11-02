import json
import copy
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import legacy_ui_server as server
from experiment_engine import DEFAULT_CONFIG, EVENT_ORDER, ExperimentStore


class NoHardware:
    def __init__(self):
        self.calls = []

    def upstream(self, path, body=None):
        self.calls.append((path, body))
        raise AssertionError("experiment routes must not call servo upstream")

    def actuate(self, *args):
        self.calls.append(("actuate", args))
        raise AssertionError("mock experiment routes must not actuate hardware")


class ExperimentHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.hardware = NoHardware()
        cls.store = ExperimentStore(Path(cls.temp.name) / "data")
        server.Handler.controller = type("ControllerStub", (), {"upstream": cls.hardware.upstream})()
        server.Handler.experiments = cls.store
        server.Handler.hardware = cls.hardware
        server.Handler.admin_token = "test-admin-token"
        try:
            cls.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        except PermissionError as error:
            cls.temp.cleanup()
            raise unittest.SkipTest(f"sandbox disallows local TCP listeners: {error}")
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.http.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=2)
        cls.temp.cleanup()

    def request(self, path, body=None, admin=False):
        headers = {}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        if admin:
            headers["X-Aria-Admin-Token"] = "test-admin-token"
        request = Request(self.base + path, data=data, headers=headers)
        with urlopen(request) as response:
            return response.status, response.headers, response.read()

    def test_admin_routes_require_token_and_mock_flow_never_calls_hardware(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + "/api/experiment/default-config")
        self.assertEqual(error.exception.code, 401)
        error.exception.close()
        status, _, raw = self.request("/api/experiment/default-config", admin=True)
        self.assertEqual(status, 200)
        config = json.loads(raw)["config"]
        self.assertEqual(config["emotions"]["fear"]["eyelid_open_percent"], 80)

        _, _, raw = self.request("/api/experiment/session/create", {
            "participant_id": "HTTP001", "mode": "main", "config": DEFAULT_CONFIG, "random_seed": 101,
        }, admin=True)
        created = json.loads(raw)
        self.assertTrue(created["ok"])
        session_id, access = created["session"]["session_id"], created["access"]
        _, _, raw = self.request("/api/experiment/session/state", {"session_id": session_id, "access": access})
        participant_view = json.loads(raw)
        self.assertNotIn("trials", participant_view)
        self.assertNotIn("intended_emotion", participant_view)

        args = {"session_id": session_id, "access": access, "protocol_version": 2}
        self.request("/api/experiment/consent", {**args, "accepted": True})
        self.request("/api/experiment/start", args)
        _, _, raw = self.request("/api/experiment/trial/start", args)
        stimulus = json.loads(raw)
        self.assertEqual(stimulus["kind"], "practice")
        for event in EVENT_ORDER:
            self.request("/api/experiment/trial/event", {**args, "event": event})
        _, _, raw = self.request("/api/experiment/answer", {
            **args, "perceived_emotion": "other", "confidence": None, "response_latency_ms": 350,
        })
        self.assertTrue(json.loads(raw)["practice_done"])
        self.assertEqual(self.hardware.calls, [])

        _, headers, csv_raw = self.request(f"/api/experiment/export/{session_id}.csv", admin=True)
        self.assertIn("text/csv", headers["Content-Type"])
        self.assertIn(b"trial_slot_id", csv_raw)

    def test_stale_config_requests_cannot_restore_old_baseline(self):
        _, _, raw = self.request("/api/experiment/config", admin=True)
        saved = json.loads(raw)
        stale = copy.deepcopy(saved["config"])
        stale["neutral"]["eyelid_open_percent"] = 65
        for path, body in [
            ("/api/experiment/config", {"config": stale}),
            ("/api/experiment/config", {"config": stale, "expected_version": "old-version"}),
            ("/api/experiment/session/create", {"participant_id": "STALE", "mode": "pilot", "config": stale}),
        ]:
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.request(path, body, admin=True)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        _, _, raw = self.request("/api/experiment/config", admin=True)
        self.assertEqual(json.loads(raw)["config"], saved["config"])
        _, _, raw = self.request("/api/experiment/config", {
            "config": saved["config"], "expected_version": saved["config_version"]}, admin=True)
        self.assertEqual(json.loads(raw)["config_version"], saved["config_version"])

    def test_participant_page_is_served_as_separate_route(self):
        status, headers, body = self.request("/experiment/participant")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("今の目は", body.decode())


if __name__ == "__main__":
    unittest.main()
