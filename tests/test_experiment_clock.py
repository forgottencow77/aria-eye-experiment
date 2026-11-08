import copy
import tempfile
import threading
import unittest
from pathlib import Path
from experiment_clock import ExperimentClock, ClockError
from experiment_engine import ExperimentStore, DEFAULT_CONFIG
from legacy_ui_server import Handler


class ExperimentClockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'clock.json'
        self.wall = 1794000000.0
        self.mono = 123.0
        self.boot = 'boot-A'
        self.clock = self.make_clock()

    def make_clock(self):
        return ExperimentClock(self.path, lambda: self.wall, lambda: self.mono, lambda: self.boot)

    def test_offline_sync_monotonic_time_and_same_boot_restart(self):
        with self.assertRaises(ClockError):
            self.clock.require_verified()
        target = 1794097000000
        self.clock.sync(target)
        self.wall -= 35000  # OS clock corrections do not jump experiment time.
        self.mono += 8
        self.assertEqual(self.clock.status()['experiment_unix_ms'], target + 8000)
        reloaded = self.make_clock()
        self.assertTrue(reloaded.status()['verified'])
        self.assertEqual(reloaded.now(), self.clock.now())
        self.boot = 'boot-B'
        self.assertFalse(self.make_clock().status()['verified'])

    def test_invalid_time_cannot_overwrite_reference(self):
        self.clock.sync(1794097000000)
        before = self.path.read_bytes()
        for value in [None, True, 'now', float('nan'), float('inf'), 0, 9999999999999]:
            with self.subTest(value=value), self.assertRaises(ClockError):
                self.clock.sync(value)
            self.assertEqual(self.path.read_bytes(), before)

    def test_session_and_command_timestamps_use_verified_reference(self):
        from legacy_ui_server import ExperimentHardware
        from test_live_control import FakeHardware, LIMITS
        from legacy_ui_server import Controller
        from live_control import LiveController
        self.clock.sync(1794097000000)
        store = ExperimentStore(Path(self.tmp.name) / 'data', wall_clock=self.clock.now,
                                clock_info=lambda: self.clock.status()['reference'])
        session, _ = store.create('CLOCK_TEST', 'pilot', DEFAULT_CONFIG)
        self.assertEqual(session['created_at'], self.clock.now())
        self.assertEqual(session['clock_reference']['source'], 'operator_browser')
        fake = FakeHardware()
        controller = Controller(LIMITS, fake.servo)
        controller.live = LiveController(controller, fake.lcd, lambda: fake.clock)
        hardware = ExperimentHardware(controller, self.clock.now)
        result = hardware.actuate(session, {'visual': DEFAULT_CONFIG['emotions']['joy']}, 'instruction')
        self.assertEqual(result['command_requested_at'], self.clock.now())
        self.assertEqual(result['command_accepted_at'], self.clock.now())

    def test_sync_rejected_during_session_or_manual_control(self):
        handler = object.__new__(Handler)
        handler.clock = self.clock
        handler.experiments = ExperimentStore(Path(self.tmp.name) / 'data')
        handler.controller = type('Controller', (), {'lock': threading.RLock(), 'live': type('Live', (), {'token': None})()})()
        handler._sync_experiment_clock({'client_unix_ms': 1794097000000})
        before = self.clock.status()['reference']
        handler.experiments.sessions['running'] = {'status': 'running'}
        with self.assertRaises(ClockError):
            handler._sync_experiment_clock({'client_unix_ms': 1794098000000})
        handler.experiments.sessions.clear()
        handler.controller.live.token = 'manual'
        with self.assertRaises(ClockError):
            handler._sync_experiment_clock({'client_unix_ms': 1794098000000})
        self.assertEqual(self.clock.status()['reference'], before)

    def test_new_boot_can_verify_before_recovering_saved_session(self):
        handler = object.__new__(Handler)
        handler.clock = self.clock
        handler.experiments = ExperimentStore(Path(self.tmp.name) / 'data')
        handler.experiments.sessions['interrupted'] = {'status': 'running'}
        handler.controller = type('Controller', (), {'lock': threading.RLock(), 'live': type('Live', (), {'token': None})()})()
        handler._sync_experiment_clock({'client_unix_ms': 1794097000000})
        self.assertTrue(self.clock.status()['verified'])
        with self.assertRaises(ClockError):
            handler._sync_experiment_clock({'client_unix_ms': 1794097001000})
