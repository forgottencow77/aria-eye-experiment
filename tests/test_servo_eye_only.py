import copy
import importlib.util
from pathlib import Path
import threading
import os
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("servo_runtime", Path(__file__).resolve().parents[1] / "servo_web_ui.py")
servo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(servo)


class EndIteration(Exception):
    pass


class FakePCA:
    def __init__(self): self.writes = []; self.releases = []
    def set_servo_us(self, channel, value): self.writes.append((channel, value))
    def release(self, channel): self.releases.append(channel)


def rig():
    instance = servo.ServoRig.__new__(servo.ServoRig)
    instance.lock = threading.RLock()
    instance.pca = FakePCA()
    instance.channels = {c['id']: dict(c) for c in servo.CHANNELS}
    instance.values = {c['id']: c['home'] for c in servo.CHANNELS}
    instance.values.update({6: 1420, 7: 1550, 8: 1680, 9: 1600})
    instance.active = {c: False for c in instance.values}
    instance.command_versions = {c: 0 for c in instance.values}
    instance.sync_active = False
    instance.sync_eye_only = False
    instance.sync_pose = {'x': 0., 'y': 0., 'blink': 0.}
    instance.sync_target_pose = dict(instance.sync_pose)
    instance.sync_last_at = 100
    instance.sync_eyebrow_preset = 'custom'
    instance.sync_target_eyebrow_preset = 'custom'
    return instance


class EyeOnlyTests(unittest.TestCase):
    def test_default_startup_keeps_original_output_policy(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(servo.startup_active_channels(), frozenset(range(6)))

    def test_maintenance_startup_preserves_released_outputs(self):
        pca = FakePCA()
        with patch.dict(os.environ, {'ARIA_STARTUP_ACTIVE_CHANNELS': '0,3'}), \
             patch.object(servo, 'PCA9685', return_value=pca), \
             patch.object(servo.ServoRig, 'load_last'), \
             patch.object(servo.threading, 'Thread'), \
             patch.object(servo.time, 'sleep'):
            instance = servo.ServoRig()
        self.assertEqual(pca.writes, [(0, 1600), (3, 1550)])
        self.assertEqual(set(pca.releases), {1, 2, 4, 5, 6, 7, 8, 9})
        self.assertEqual({c for c, active in instance.active.items() if active}, {0, 3})

    def test_invalid_startup_output_policy_fails_before_hardware_initialization(self):
        with patch.dict(os.environ, {'ARIA_STARTUP_ACTIVE_CHANNELS': '10'}), \
             patch.object(servo, 'PCA9685') as pca:
            with self.assertRaises(ValueError): servo.ServoRig()
            pca.assert_not_called()

    def test_legacy_default_still_returns_ten_channels(self):
        self.assertEqual(set(servo.ServoRig._target_for_pose(None, {'x': 0, 'y': 0, 'blink': 0})), set(range(10)))

    def test_eye_only_frame_omits_brows_and_matches_all_calibration_endpoints(self):
        limits = {0:(1200,1700),1:(950,2200),2:(1100,2000),3:(1450,1950),4:(850,2000),5:(1000,2200)}
        for x in [-1, 0, 1]:
            for y in [-1, 0, 1]:
                for blink in [0, .5, 1]:
                    frame = servo.ServoRig._target_for_pose(None, {'x':x,'y':y,'blink':blink}, eye_only=True)
                    self.assertEqual(set(frame), set(range(6)))
                    for c,v in frame.items(): self.assertTrue(limits[c][0] <= v <= limits[c][1])

    def test_sync_does_not_reset_custom_brow_preset(self):
        instance = rig()
        frame = instance.sync({'gaze':{'x':1,'y':1},'blink':1,'eyeOnly':True})
        self.assertEqual(set(frame),set(range(6)))
        self.assertEqual(instance.sync_target_eyebrow_preset,'custom')

    def test_active_loop_neither_writes_nor_releases_brows(self):
        instance = rig()
        before = copy.deepcopy(instance.values)
        with patch.object(servo.time,'monotonic',return_value=100):
            instance.sync({'gaze':{'x':1,'y':1},'blink':1,'eyeOnly':True})
            with patch.object(servo.time,'sleep',side_effect=EndIteration):
                with self.assertRaises(EndIteration):instance._sync_loop()
        self.assertTrue(instance.pca.writes)
        self.assertTrue(all(c<6 for c,_ in instance.pca.writes))
        self.assertEqual(instance.pca.releases,[])
        self.assertEqual({c:instance.values[c] for c in range(6,10)},{c:before[c] for c in range(6,10)})

    def test_stale_loop_preserves_brows(self):
        instance=rig()
        with patch.object(servo.time,'monotonic',return_value=100):instance.sync({'eyeOnly':True})
        with patch.object(servo.time,'monotonic',return_value=103),patch.object(servo.time,'sleep',side_effect=EndIteration):
            with self.assertRaises(EndIteration):instance._sync_loop()
        self.assertFalse(instance.sync_active)
        self.assertEqual(instance.pca.releases,[])
        self.assertEqual(instance.sync_target_eyebrow_preset,'custom')
        self.assertEqual(set(instance.sync_target),set(range(6)))

    def test_scope_must_be_boolean(self):
        with self.assertRaises(ValueError):rig().sync({'eyeOnly':'true'})


if __name__ == '__main__':unittest.main()
