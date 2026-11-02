#!/usr/bin/env python3
import argparse
import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    import smbus2
except ImportError as exc:
    smbus2 = None
    SMBUS_IMPORT_ERROR = exc
else:
    SMBUS_IMPORT_ERROR = None

PCA9685_ADDR = 0x40
MODE1 = 0x00
MODE2 = 0x01
PRESCALE = 0xFE
LED0_ON_L = 0x06

SERVO_FREQ = 50
TICKS_PER_PERIOD = 4096
US_PER_PERIOD = 1_000_000 / SERVO_FREQ

LOG_PATH = Path(__file__).with_name('servo_position_log.jsonl')
PRESET_PATH = Path(__file__).with_name('servo_last_position.json')

CHANNELS = [
    {'id': 0, 'name': '1 右目 上下', 'short': 'R-UD', 'min': 250, 'max': 2600, 'home': 1600},
    {'id': 1, 'name': '2 右目 左右', 'short': 'R-LR', 'min': 250, 'max': 2600, 'home': 1450},
    {'id': 2, 'name': '3 左目 左右', 'short': 'L-LR', 'min': 250, 'max': 2600, 'home': 1600},
    {'id': 3, 'name': '4 左目 上下', 'short': 'L-UD', 'min': 250, 'max': 2600, 'home': 1550},
    {'id': 4, 'name': '5 右目 瞼', 'short': 'R-LID', 'min': 250, 'max': 2600, 'home': 850},
    {'id': 5, 'name': '6 左目 瞼', 'short': 'L-LID', 'min': 250, 'max': 2600, 'home': 2200},
    {'id': 6, 'name': '右眉の左右の傾き', 'short': 'R-BROW-TILT', 'min': 1291, 'max': 1609, 'home': 1450, 'min_label': '困り眉', 'max_label': '反対傾き'},
    {'id': 7, 'name': '右眉の真ん中', 'short': 'R-BROW-MID', 'min': 800, 'max': 2300, 'home': 1450, 'min_label': '眉が下がる', 'max_label': '眉が上がる'},
    {'id': 8, 'name': '左眉の左右の傾き', 'short': 'L-BROW-TILT', 'min': 1300, 'max': 2000, 'home': 1500, 'min_label': '反対傾き', 'max_label': '困り眉'},
    {'id': 9, 'name': '左眉の真ん中', 'short': 'L-BROW-MID', 'min': 1100, 'max': 2300, 'home': 1500, 'min_label': '眉が上がる', 'max_label': '眉が下がる'},
]

DEFAULT_EYEBROW_PRESET = 'relaxed'
POSE_PRESETS = {
    'joy': {
        0: 1600, 1: 1450, 2: 1600, 3: 1550, 4: 2000, 5: 1000,
        6: 1450, 7: 2100, 8: 1500, 9: 1150,
    },
    'anger': {
        0: 1550, 1: 1450, 2: 1600, 3: 1500, 4: 1700, 5: 1300,
        6: 1570, 7: 1320, 8: 1300, 9: 1660,
    },
    'sadness': {
        0: 1300, 1: 1450, 2: 1600, 3: 1800, 4: 2100, 5: 900,
        6: 1291, 7: 1620, 8: 2000, 9: 1420,
    },
    'relaxed': {
        0: 1650, 1: 1450, 2: 1600, 3: 1500, 4: 900, 5: 2100,
        6: 1450, 7: 1450, 8: 1500, 9: 1500,
    },
}
EYEBROW_PRESETS = {
    preset: {channel: values[channel] for channel in range(6, 10)}
    for preset, values in POSE_PRESETS.items()
}
EYEBROW_CHANNEL_IDS = frozenset(EYEBROW_PRESETS[DEFAULT_EYEBROW_PRESET])
STARTUP_RELEASE_CHANNEL_IDS = EYEBROW_CHANNEL_IDS

# Observer-facing calibration. Web gaze is normalized to -1..1 and uses the
# measured mechanical envelope directly.
# Eyelid endpoint polarity is based on the reported physical behavior:
# 850/2200 us = open; 2000/1000 us = closed. Keep blink=0 open,
# blink=1 closed so ordinary clients and the stale fallback agree.
SYNC_CENTER = {0: 1600, 1: 1450, 2: 1600, 3: 1550, 4: 850, 5: 2200}
SYNC_LEFT = {1: 950, 2: 1100}
SYNC_RIGHT = {1: 2200, 2: 2000}
SYNC_DOWN = {0: 1700, 3: 1450}
SYNC_UP = {0: 1200, 3: 1950}
SYNC_CLOSED = {4: 2000, 5: 1000}
SYNC_EYE_ENVELOPE = 1.0
SYNC_STALE_SECONDS = 1.8
SYNC_GAZE_ALPHA = 0.25
SYNC_BLINK_ALPHA = 0.35
SYNC_EYEBROW_ALPHA = 0.35
SYNC_POSE_EPSILON = 0.002


def clamp(value, lo, hi):
    return max(lo, min(hi, int(value)))


def clamp_float(value, lo, hi):
    return max(lo, min(hi, float(value)))


def lerp_int(a, b, t):
    return int(round(a + (b - a) * clamp_float(t, 0.0, 1.0)))


def calibrated_axis(center, negative, positive, value):
    value = clamp_float(value, -1.0, 1.0) * SYNC_EYE_ENVELOPE
    if value < 0:
        return lerp_int(center, negative, -value)
    return lerp_int(center, positive, value)


def uncalibrated_axis(center, negative, positive, pulse):
    pulse = float(pulse)
    delta = pulse - center
    negative_delta = negative - center
    positive_delta = positive - center
    if negative_delta and delta * negative_delta > 0:
        raw = delta / negative_delta
        return clamp_float(-raw / SYNC_EYE_ENVELOPE, -1.0, 0.0)
    if positive_delta and delta * positive_delta > 0:
        raw = delta / positive_delta
        return clamp_float(raw / SYNC_EYE_ENVELOPE, 0.0, 1.0)
    return 0.0


def transition_progress(start, end, value):
    if start == end:
        return 0.0
    return clamp_float((float(value) - start) / (end - start), 0.0, 1.0)


def us_to_ticks(us):
    return int(round((us / US_PER_PERIOD) * TICKS_PER_PERIOD))



def ease_in_out(t):
    return 0.5 - 0.5 * math.cos(math.pi * t)


def normalize_values(values):
    out = {}
    for c in CHANNELS:
        cid = c['id']
        raw = values.get(str(cid), values.get(cid, c['home']))
        out[cid] = clamp(raw, c['min'], c['max'])
    return out


class PCA9685:
    def __init__(self, bus_no=1, addr=PCA9685_ADDR):
        if smbus2 is None:
            raise RuntimeError(f'smbus2 import failed: {SMBUS_IMPORT_ERROR!r}')
        self.bus = smbus2.SMBus(bus_no)
        self.addr = addr
        self.init_50hz()

    def write8(self, reg, value):
        self.bus.write_byte_data(self.addr, reg, value & 0xFF)

    def read8(self, reg):
        return self.bus.read_byte_data(self.addr, reg)

    def init_50hz(self):
        prescale = int(round(25_000_000 / (TICKS_PER_PERIOD * SERVO_FREQ)) - 1)
        oldmode = self.read8(MODE1)
        # Recover even when a previous interrupted init left SLEEP asserted.
        awake = (oldmode & 0x6F) | 0x21  # clear RESTART/SLEEP, enable AI + ALLCALL
        sleep = awake | 0x10
        self.write8(MODE1, sleep)
        self.write8(PRESCALE, prescale)
        self.write8(MODE1, awake)
        time.sleep(0.005)
        self.write8(MODE1, awake | 0x80)    # restart; bit clears automatically
        self.write8(MODE2, 0x04)            # totem-pole outputs

    def set_pwm(self, channel, on, off):
        reg = LED0_ON_L + 4 * channel
        data = [on & 0xFF, (on >> 8) & 0x0F, off & 0xFF, (off >> 8) & 0x0F]
        self.bus.write_i2c_block_data(self.addr, reg, data)

    def set_servo_us(self, channel, us):
        self.set_pwm(channel, 0, us_to_ticks(us))

    def release(self, channel):
        self.set_pwm(channel, 0, 0)


def startup_active_channels():
    """Allow a maintenance restart to preserve the current PWM enable state."""
    value = os.environ.get('ARIA_STARTUP_ACTIVE_CHANNELS')
    if value is None:
        return frozenset(c['id'] for c in CHANNELS) - STARTUP_RELEASE_CHANNEL_IDS
    active = frozenset(int(part.strip()) for part in value.split(',') if part.strip())
    if not active.issubset(c['id'] for c in CHANNELS):
        raise ValueError('ARIA_STARTUP_ACTIVE_CHANNELS contains an unknown channel')
    return active


class ServoRig:
    def __init__(self):
        initial_active = startup_active_channels()
        self.pca = PCA9685()
        self.channels = {c['id']: dict(c) for c in CHANNELS}
        self.values = {c['id']: c['home'] for c in CHANNELS}
        self.active = {c['id']: False for c in CHANNELS}
        self.command_versions = {c['id']: 0 for c in CHANNELS}
        self.lock = threading.RLock()
        self.sync_target = dict(SYNC_CENTER)
        self.sync_pose = {'x': 0.0, 'y': 0.0, 'blink': 0.0}
        self.sync_target_pose = dict(self.sync_pose)
        self.sync_fallback_pose = {'x': 0.0, 'y': 0.0, 'blink': 0.0}
        self.sync_eyebrow_preset = DEFAULT_EYEBROW_PRESET
        self.sync_target_eyebrow_preset = DEFAULT_EYEBROW_PRESET
        self.sync_last_at = 0.0
        self.sync_active = False
        self.sync_eye_only = False
        self.load_last()
        self.sync_pose = self._infer_sync_pose()
        self.sync_target_pose = dict(self.sync_pose)
        for channel, us in self.values.items():
            if channel not in initial_active:
                self.pca.release(channel)
            else:
                self.pca.set_servo_us(channel, us)
                self.active[channel] = True
            time.sleep(0.03)
        self.sync_thread = threading.Thread(target=self._sync_loop, name='aria-servo-sync', daemon=True)
        self.sync_thread.start()

    def load_last(self):
        if not PRESET_PATH.exists():
            return
        try:
            data = json.loads(PRESET_PATH.read_text())
            for c in CHANNELS:
                cid = c['id']
                if str(cid) in data.get('values', {}):
                    self.values[cid] = clamp(data['values'][str(cid)], c['min'], c['max'])
        except Exception:
            pass

    def save_last(self):
        data = {'saved_at': time.time(), 'values': self.values}
        PRESET_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def state(self):
        with self.lock:
            values = dict(self.values)
            active = dict(self.active)
            sync = {
                'active': self.sync_active,
                'eye_only': self.sync_eye_only,
                'last_update_age': max(0.0, time.monotonic() - self.sync_last_at) if self.sync_last_at else None,
                'target': dict(self.sync_target),
                'pose': dict(self.sync_pose),
                'target_pose': dict(self.sync_target_pose),
                'fallback_pose': dict(self.sync_fallback_pose),
                'eyebrow_preset': self.sync_eyebrow_preset,
                'target_eyebrow_preset': self.sync_target_eyebrow_preset,
            }
        return {
            'ok': True,
            'channels': CHANNELS,
            'values': values,
            'active': active,
            'sync': sync,
            'log_path': str(LOG_PATH),
            'preset_path': str(PRESET_PATH),
            'saved_positions': self.saved_positions(),
        }

    def set_us(self, channel, us):
        with self.lock:
            value, _ = self._set_us_locked(channel, us)
            self.save_last()
        return value

    def _set_us_locked(self, channel, us):
        self.sync_active = False
        channel = int(channel)
        cfg = self.channels[channel]
        value = clamp(us, cfg['min'], cfg['max'])
        self.values[channel] = value
        self.command_versions[channel] += 1
        version = self.command_versions[channel]
        self.pca.set_servo_us(channel, value)
        self.active[channel] = True
        if channel in EYEBROW_CHANNEL_IDS:
            self.sync_eyebrow_preset = 'custom'
            self.sync_target_eyebrow_preset = 'custom'
        return value, version

    def set_us_and_release(self, channel, us):
        channel = int(channel)
        with self.lock:
            start = self.values[channel]
            value, version = self._set_us_locked(channel, us)
            self.save_last()
        settle_seconds = max(0.25, min(1.25, 0.18 + abs(value - start) / 1500))
        time.sleep(settle_seconds)
        with self.lock:
            if self.command_versions[channel] == version and not self.sync_active:
                self.pca.release(channel)
                self.active[channel] = False
        return value

    def set_eyebrow_preset_and_release(self, preset):
        preset = str(preset or '').strip().lower()
        if preset not in EYEBROW_PRESETS:
            raise ValueError(f'unknown eyebrow preset: {preset}')
        with self.lock:
            versions = {}
            max_delta = 0
            for channel, target in EYEBROW_PRESETS[preset].items():
                max_delta = max(max_delta, abs(target - self.values[channel]))
                _, versions[channel] = self._set_us_locked(channel, target)
            self.sync_eyebrow_preset = preset
            self.sync_target_eyebrow_preset = preset
            self.save_last()
        settle_seconds = max(0.25, min(1.25, 0.18 + max_delta / 1500))
        time.sleep(settle_seconds)
        with self.lock:
            for channel, version in versions.items():
                if self.command_versions[channel] != version or self.sync_active:
                    continue
                self.pca.release(channel)
                self.active[channel] = False
        return preset

    def nudge(self, channel, delta):
        channel = int(channel)
        return self.set_us(channel, self.values[channel] + int(delta))

    def nudge_and_release(self, channel, delta):
        channel = int(channel)
        with self.lock:
            target = self.values[channel] + int(delta)
        return self.set_us_and_release(channel, target)

    def center(self, channel=None):
        if channel is None:
            for cfg in CHANNELS:
                self.set_us(cfg['id'], cfg['home'])
                time.sleep(0.03)
        else:
            cfg = self.channels[int(channel)]
            self.set_us(cfg['id'], cfg['home'])

    def release(self, channel=None):
        with self.lock:
            self.sync_active = False
            if channel is None:
                ids = [c['id'] for c in CHANNELS]
            else:
                ids = [int(channel)]
            for cid in ids:
                self.command_versions[cid] += 1
                self.pca.release(cid)
                self.active[cid] = False

    def sync(self, payload):
        gaze = payload.get('gaze') if isinstance(payload.get('gaze'), dict) else {}
        x = clamp_float(gaze.get('x', 0.0), -1.0, 1.0)
        y = clamp_float(gaze.get('y', 0.0), -1.0, 1.0)
        blink = clamp_float(payload.get('blink', 0.0), 0.0, 1.0)
        eye_only = payload.get('eyeOnly', False)
        if not isinstance(eye_only, bool):
            raise ValueError('eyeOnly must be a boolean')
        fallback = payload.get('fallbackPose', {'x': 0.0, 'y': 0.0, 'blink': 0.0})
        if not isinstance(fallback, dict) or set(fallback) != {'x', 'y', 'blink'}:
            raise ValueError('fallbackPose must contain x, y and blink')
        for key, value in fallback.items():
            low = 0 if key == 'blink' else -1
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= 1:
                raise ValueError('fallbackPose is outside the calibrated range')
        if 'fallbackPose' in payload and not eye_only:
            raise ValueError('fallbackPose requires eyeOnly')
        eyebrow_preset = str(payload.get('eyebrowPreset', DEFAULT_EYEBROW_PRESET)).strip().lower()
        if eyebrow_preset not in EYEBROW_PRESETS:
            eyebrow_preset = DEFAULT_EYEBROW_PRESET

        with self.lock:
            if not self.sync_active:
                self.sync_pose = self._infer_sync_pose()
            self.sync_target_pose = {'x': x, 'y': y, 'blink': blink}
            self.sync_fallback_pose = dict(fallback)
            self.sync_eye_only = eye_only
            if not eye_only:
                self.sync_target_eyebrow_preset = eyebrow_preset
            self.sync_target = self._target_for_pose(self.sync_target_pose, eyebrow_preset, eye_only=eye_only)
            self.sync_last_at = time.monotonic()
            self.sync_active = True
        return dict(self.sync_target)

    def _target_for_pose(self, pose, eyebrow_preset=DEFAULT_EYEBROW_PRESET, *, eye_only=False):
        target = dict(SYNC_CENTER)
        target[1] = calibrated_axis(SYNC_CENTER[1], SYNC_LEFT[1], SYNC_RIGHT[1], pose['x'])
        target[2] = calibrated_axis(SYNC_CENTER[2], SYNC_LEFT[2], SYNC_RIGHT[2], pose['x'])
        target[0] = calibrated_axis(SYNC_CENTER[0], SYNC_DOWN[0], SYNC_UP[0], pose['y'])
        target[3] = calibrated_axis(SYNC_CENTER[3], SYNC_DOWN[3], SYNC_UP[3], pose['y'])
        target[4] = lerp_int(SYNC_CENTER[4], SYNC_CLOSED[4], pose['blink'])
        target[5] = lerp_int(SYNC_CENTER[5], SYNC_CLOSED[5], pose['blink'])
        if not eye_only:
            target.update(EYEBROW_PRESETS.get(eyebrow_preset, EYEBROW_PRESETS[DEFAULT_EYEBROW_PRESET]))
        return target

    def _infer_sync_pose(self):
        x = (
            uncalibrated_axis(SYNC_CENTER[1], SYNC_LEFT[1], SYNC_RIGHT[1], self.values[1])
            + uncalibrated_axis(SYNC_CENTER[2], SYNC_LEFT[2], SYNC_RIGHT[2], self.values[2])
        ) / 2
        y = (
            uncalibrated_axis(SYNC_CENTER[0], SYNC_DOWN[0], SYNC_UP[0], self.values[0])
            + uncalibrated_axis(SYNC_CENTER[3], SYNC_DOWN[3], SYNC_UP[3], self.values[3])
        ) / 2
        blink = (
            transition_progress(SYNC_CENTER[4], SYNC_CLOSED[4], self.values[4])
            + transition_progress(SYNC_CENTER[5], SYNC_CLOSED[5], self.values[5])
        ) / 2
        return {'x': x, 'y': y, 'blink': blink}

    def _sync_loop(self):
        period = 1 / 50
        while True:
            with self.lock:
                if self.sync_active:
                    stale = time.monotonic() - self.sync_last_at > SYNC_STALE_SECONDS
                    target_pose = self.sync_fallback_pose if stale else self.sync_target_pose
                    target_eyebrow_preset = (
                        DEFAULT_EYEBROW_PRESET if stale else self.sync_target_eyebrow_preset
                    )
                    settled = True
                    for key in ('x', 'y', 'blink'):
                        current = self.sync_pose[key]
                        goal = target_pose[key]
                        error = goal - current
                        if abs(error) <= SYNC_POSE_EPSILON:
                            self.sync_pose[key] = goal
                            continue
                        alpha = SYNC_BLINK_ALPHA if key == 'blink' else SYNC_GAZE_ALPHA
                        self.sync_pose[key] = current + error * alpha
                        settled = False

                    frame = self._target_for_pose(self.sync_pose, target_eyebrow_preset, eye_only=self.sync_eye_only)
                    for cid, value in frame.items():
                        next_value = value
                        if cid in EYEBROW_CHANNEL_IDS:
                            error = value - self.values[cid]
                            if abs(error) <= 1:
                                next_value = value
                            else:
                                step = int(round(error * SYNC_EYEBROW_ALPHA))
                                if step == 0:
                                    step = 1 if error > 0 else -1
                                next_value = self.values[cid] + step
                                settled = False
                        if next_value == self.values[cid]:
                            continue
                        self.values[cid] = next_value
                        self.command_versions[cid] += 1
                        self.pca.set_servo_us(cid, next_value)
                        self.active[cid] = True
                    for cid in (() if self.sync_eye_only else EYEBROW_CHANNEL_IDS):
                        if self.active[cid] and self.values[cid] == frame[cid]:
                            self.command_versions[cid] += 1
                            self.pca.release(cid)
                            self.active[cid] = False
                    if stale and settled:
                        self.sync_target = self._target_for_pose(
                            self.sync_fallback_pose,
                            DEFAULT_EYEBROW_PRESET,
                            eye_only=self.sync_eye_only,
                        )
                        self.sync_target_pose = dict(self.sync_fallback_pose)
                        if not self.sync_eye_only:
                            self.sync_eyebrow_preset = DEFAULT_EYEBROW_PRESET
                            self.sync_target_eyebrow_preset = DEFAULT_EYEBROW_PRESET
                        for cid in (() if self.sync_eye_only else EYEBROW_CHANNEL_IDS):
                            self.command_versions[cid] += 1
                            self.pca.release(cid)
                            self.active[cid] = False
                        self.sync_active = False
                    elif not self.sync_eye_only:
                        self.sync_eyebrow_preset = target_eyebrow_preset
            time.sleep(period)

    def record(self, label=''):
        entry = {
            'time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'label': str(label or ''),
            'values': dict(self.values),
        }
        with LOG_PATH.open('a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        return entry


    def saved_positions(self, limit=80):
        poses = []
        if LOG_PATH.exists():
            for idx, line in enumerate(LOG_PATH.read_text().splitlines()):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    values = normalize_values(entry.get('values', {}))
                    label = entry.get('label') or f'pose {idx + 1}'
                    poses.append({
                        'id': idx,
                        'label': label,
                        'time': entry.get('time', ''),
                        'values': values,
                    })
                except Exception:
                    continue
        return poses[-limit:]

    def pose_by_id(self, pose_id):
        pose_id = int(pose_id)
        for pose in self.saved_positions(limit=10000):
            if int(pose['id']) == pose_id:
                return normalize_values(pose['values'])
        raise ValueError(f'pose id not found: {pose_id}')

    def move_to_values(self, target, duration=0.6, fps=45):
        target = normalize_values(target)
        duration = max(0.05, min(20.0, float(duration)))
        frames = max(1, int(duration * fps))
        with self.lock:
            self.sync_active = False
            start = dict(self.values)
            for i in range(1, frames + 1):
                t = ease_in_out(i / frames)
                frame = {}
                for cfg in CHANNELS:
                    cid = cfg['id']
                    frame[cid] = int(round(start[cid] + (target[cid] - start[cid]) * t))
                for cid, us in frame.items():
                    self.values[cid] = us
                    self.command_versions[cid] += 1
                    self.pca.set_servo_us(cid, us)
                    self.active[cid] = True
                time.sleep(1 / fps)
            self.values = dict(target)
            self.save_last()
        return dict(self.values)

    def move_saved(self, from_id=None, to_id=None, duration=0.6):
        if to_id is None:
            raise ValueError('to_id is required')
        if from_id is not None and from_id != 'current':
            self.move_to_values(self.pose_by_id(from_id), duration)
            time.sleep(0.08)
        self.move_to_values(self.pose_by_id(to_id), duration)
        return dict(self.values)


HTML = r'''<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Aria Brow Control</title>
<style>
:root { color-scheme: light; --brat:#8ace00; --ink:#0b0b0b; --paper:#d7ff86; --white:#f7ffe8; --bad:#ff4f43; }
* { box-sizing: border-box; }
body { margin:0; min-height:100vh; background:var(--brat); color:var(--ink); font:15px/1.35 Arial,Helvetica,sans-serif; }
button,input { font:inherit; }
main { max-width:1180px; margin:0 auto; padding:24px; }
header { display:flex; justify-content:space-between; gap:20px; align-items:flex-start; margin-bottom:24px; }
h1 { margin:0; font-size:clamp(48px,9vw,112px); line-height:.78; letter-spacing:-.09em; font-weight:900; text-transform:lowercase; }
.sub { margin:14px 0 0 5px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
.status { border:2px solid var(--ink); background:var(--paper); padding:11px 14px; min-width:190px; box-shadow:5px 5px 0 var(--ink); font-weight:800; text-transform:uppercase; }
.status b { display:inline-block; width:9px; height:9px; margin-right:7px; border-radius:50%; background:var(--ink); }
.controls { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:0 0 24px; }
.preset-panel { margin:0 0 24px; padding:16px; border:3px solid var(--ink); background:rgba(215,255,134,.5); box-shadow:8px 8px 0 var(--ink); }
.preset-head { display:flex; justify-content:space-between; gap:12px; margin-bottom:12px; font-size:13px; font-weight:900; text-transform:uppercase; }
.presets { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; }
.preset { min-height:76px; display:grid; place-content:center; gap:2px; font-size:30px; line-height:1; }
.preset small { font-size:11px; letter-spacing:.08em; }
.preset[data-preset="joy"] { border-left:10px solid #e8c84f; }
.preset[data-preset="anger"] { border-left:10px solid #e96761; }
.preset[data-preset="sadness"] { border-left:10px solid #62a9e8; }
.preset[data-preset="relaxed"] { border-left:10px solid #72c993; background:var(--white); }
.preset[aria-pressed="true"] { background:var(--ink); color:var(--brat); }
button { appearance:none; border:2px solid var(--ink); background:var(--paper); color:var(--ink); padding:12px 14px; min-height:46px; font-weight:900; text-transform:uppercase; cursor:pointer; box-shadow:4px 4px 0 var(--ink); }
button:hover { background:var(--white); transform:translate(-1px,-1px); box-shadow:6px 6px 0 var(--ink); }
button:active { transform:translate(3px,3px); box-shadow:1px 1px 0 var(--ink); }
button:disabled { opacity:.45; cursor:wait; transform:none; }
button.release { background:var(--ink); color:var(--brat); }
.grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:20px; }
.card { background:rgba(215,255,134,.82); border:3px solid var(--ink); padding:18px; box-shadow:8px 8px 0 var(--ink); }
.card-top { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }
.channel { font-size:46px; line-height:.9; font-weight:900; letter-spacing:-.06em; }
.name { margin-top:8px; font-size:17px; font-weight:900; }
.short { font-size:12px; font-weight:900; letter-spacing:.08em; text-transform:uppercase; }
.power { display:inline-flex; align-items:center; gap:7px; padding:5px 8px; border:2px solid var(--ink); background:var(--white); font-size:12px; font-weight:900; text-transform:uppercase; }
.power::before { content:""; width:8px; height:8px; border-radius:50%; background:var(--bad); }
.power.on::before { background:var(--ink); }
.readout { display:flex; align-items:baseline; gap:8px; margin:22px 0 8px; }
.us { font-size:clamp(42px,7vw,72px); line-height:.8; font-weight:900; letter-spacing:-.06em; }
.unit { font-size:14px; font-weight:900; }
.limits { display:flex; justify-content:space-between; margin:7px 0 15px; font-size:12px; font-weight:900; text-transform:uppercase; }
.limits span { display:grid; gap:2px; }
.limits span:last-child { text-align:right; }
.limits small { font-size:11px; text-transform:none; }
input[type=range] { width:100%; height:34px; margin:0; accent-color:var(--ink); cursor:ew-resize; }
.direct { display:grid; grid-template-columns:1fr auto; gap:8px; margin:14px 0; }
.direct input { min-width:0; border:2px solid var(--ink); background:var(--white); color:var(--ink); padding:10px 12px; font-weight:900; }
.buttons { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; }
.buttons.secondary { grid-template-columns:1fr 1fr; margin-top:8px; }
.note { margin:24px 0 0; border-top:2px solid var(--ink); padding-top:12px; font-size:13px; font-weight:700; }
@media (max-width:760px){
  main { padding:16px; }
  header { display:block; }
  .status { margin-top:20px; }
  .grid { grid-template-columns:1fr; }
  .controls { grid-template-columns:1fr; }
  .presets { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .buttons { grid-template-columns:repeat(2,minmax(0,1fr)); }
}
</style>
</head>
<body>
<main>
<header>
  <div>
    <h1>brow control</h1>
    <div class="sub">aria / pca9685 / channel 6—9</div>
  </div>
  <div class="status" id="status">接続中...</div>
</header>
<section class="controls">
  <button onclick="homeBrows()">default all</button>
  <button onclick="refresh()">refresh</button>
  <button class="release" onclick="releaseBrows()">release all 4</button>
</section>
<section class="preset-panel">
  <div class="preset-head"><span>眉のプリセット</span><span>default: relaxed</span></div>
  <div class="presets">
    <button class="preset" data-preset="joy" aria-pressed="false" onclick="applyPreset('joy')">喜<small>joy</small></button>
    <button class="preset" data-preset="anger" aria-pressed="false" onclick="applyPreset('anger')">怒<small>anger</small></button>
    <button class="preset" data-preset="sadness" aria-pressed="false" onclick="applyPreset('sadness')">哀<small>sadness</small></button>
    <button class="preset" data-preset="relaxed" aria-pressed="false" onclick="applyPreset('relaxed')">楽<small>relaxed / default</small></button>
  </div>
</section>
<section class="grid" id="cards"></section>
<p class="note">スライダーはキャリブレーション済みの下限〜上限に固定。目標までの移動時間を見積もり、到達後にPWMを自動releaseする。異音・引っ掛かり・発熱があれば release を押す。</p>
</main>
<script>
let state = null;
const BROW_IDS = [6,7,8,9];
let busy = false;
async function api(path, body=null){
  const opt = body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {};
  const res = await fetch(path, opt);
  if(!res.ok) throw new Error(await res.text());
  return await res.json();
}
function browChannels(){ return state.channels.filter(ch => BROW_IDS.includes(ch.id)); }
function percent(ch, value){ return Math.round((value-ch.min) * 100 / (ch.max-ch.min)); }
function cardHtml(ch){
  const value = Number(state.values[ch.id]);
  const active = Boolean(state.active && state.active[ch.id]);
  return `
    <article class="card" id="card-${ch.id}">
      <div class="card-top">
        <div>
          <div class="channel">id ${ch.id}</div>
          <div class="name">${ch.name}</div>
          <div class="short">${ch.short}</div>
        </div>
        <span class="power ${active?'on':''}">${active?'pwm on':'released'}</span>
      </div>
      <div class="readout"><span class="us">${value}</span><span class="unit">µs / ${percent(ch,value)}%</span></div>
      <input type="range" min="${ch.min}" max="${ch.max}" step="1" value="${value}"
        aria-label="ID ${ch.id} pulse width" oninput="preview(${ch.id},this.value)" onchange="setServo(${ch.id},this.value)">
      <div class="limits">
        <span>下限 ${ch.min}<small>${ch.min_label||'min'}</small></span>
        <span>default ${ch.home}<small>基準位置</small></span>
        <span>上限 ${ch.max}<small>${ch.max_label||'max'}</small></span>
      </div>
      <div class="direct">
        <input id="direct-${ch.id}" type="number" min="${ch.min}" max="${ch.max}" value="${value}" aria-label="ID ${ch.id} direct value">
        <button onclick="setServo(${ch.id},document.getElementById('direct-${ch.id}').value)">set</button>
      </div>
      <div class="buttons">
        <button onclick="setServo(${ch.id},${ch.min})">min</button>
        <button onclick="setServo(${ch.id},${ch.home})">default</button>
        <button onclick="setServo(${ch.id},${ch.max})">max</button>
        <button class="release" onclick="releaseOne(${ch.id})">release</button>
      </div>
      <div class="buttons secondary">
        <button onclick="nudge(${ch.id},-10)">-10</button>
        <button onclick="nudge(${ch.id},10)">+10</button>
      </div>
    </article>`;
}
function render(){
  if(!state) return;
  document.getElementById('status').innerHTML = `<b></b>live<br>${new Date().toLocaleTimeString()}`;
  document.getElementById('cards').innerHTML = browChannels().map(cardHtml).join('');
  document.querySelectorAll('[data-preset]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.preset === state.sync.eyebrow_preset));
  });
}
async function refresh(){
  try { state = await api('/api/state'); render(); }
  catch(e){ document.getElementById('status').textContent = 'error: ' + e.message; }
}
function preview(id, raw){
  const ch = state.channels.find(item => item.id === id);
  const card = document.getElementById(`card-${id}`);
  if(!card || !ch) return;
  card.querySelector('.us').textContent = raw;
  card.querySelector('.unit').textContent = `µs / ${percent(ch,Number(raw))}%`;
  document.getElementById(`direct-${id}`).value = raw;
}
async function run(action){
  if(busy) return;
  busy = true;
  document.querySelectorAll('button').forEach(button => button.disabled = true);
  try { await action(); render(); }
  catch(error){ alert(error.message); await refresh(); }
  finally { busy = false; document.querySelectorAll('button').forEach(button => button.disabled = false); }
}
async function setServo(id, raw){
  await run(async () => { state = await api('/api/set', {channel:id, us:Number(raw)}); });
}
async function nudge(id, delta){ await run(async () => { state = await api('/api/nudge', {channel:id, delta}); }); }
async function releaseOne(id){ await run(async () => { state = await api('/api/release', {channel:id}); }); }
async function applyPreset(preset){ await run(async () => { state = await api('/api/preset', {preset}); }); }
async function homeBrows(){
  await run(async () => {
    for(const ch of browChannels()) state = await api('/api/set', {channel:ch.id, us:ch.home});
  });
}
async function releaseBrows(){
  await run(async () => {
    for(const id of BROW_IDS) state = await api('/api/release', {channel:id});
  });
}
refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>'''


rig = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print('%s - %s' % (self.address_string(), fmt % args))

    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        length = int(self.headers.get('Content-Length', '0') or '0')
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode('utf-8'))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/':
            data = HTML.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == '/api/state':
            self.send_json(rig.state())
            return
        self.send_json({'ok': False, 'error': 'not found'}, 404)

    def do_POST(self):
        try:
            body = self.read_json()
            path = urlparse(self.path).path
            if path == '/api/set':
                rig.set_us_and_release(body['channel'], body['us'])
                self.send_json(rig.state())
            elif path == '/api/preset':
                rig.set_eyebrow_preset_and_release(body.get('preset'))
                self.send_json(rig.state())
            elif path == '/api/nudge':
                rig.nudge_and_release(body['channel'], body['delta'])
                self.send_json(rig.state())
            elif path == '/api/center':
                rig.center(body.get('channel'))
                self.send_json(rig.state())
            elif path == '/api/release':
                rig.release(body.get('channel'))
                self.send_json(rig.state())
            elif path == '/api/record':
                entry = rig.record(body.get('label', ''))
                self.send_json({'ok': True, 'entry': entry, 'state': rig.state()})
            elif path == '/api/move_saved':
                rig.move_saved(body.get('from_id'), body.get('to_id'), body.get('duration', 0.7))
                self.send_json(rig.state())
            elif path == '/api/sync':
                target = rig.sync(body)
                self.send_json({'ok': True, 'target': target})
            else:
                self.send_json({'ok': False, 'error': 'not found'}, 404)
        except Exception as exc:
            self.send_json({'ok': False, 'error': repr(exc)}, 500)


def main():
    global rig
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8768)
    args = parser.parse_args()
    rig = ServoRig()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'servo web ui: http://{args.host}:{args.port}/')
    print(f'log file: {LOG_PATH}')
    server.serve_forever()


if __name__ == '__main__':
    main()
