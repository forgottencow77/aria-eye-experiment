"""Offline experiment UTC anchored to an operator clock and monotonic time."""
import json
import math
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class ClockError(ValueError):
    pass


def boot_id():
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        # No persisted reference is trusted across server restarts on other OSes.
        return None


class ExperimentClock:
    def __init__(self, path, wall=time.time, monotonic=time.monotonic, boot=boot_id):
        self.path = Path(path)
        self.wall = wall
        self.monotonic = monotonic
        self.boot_id = boot()
        self.lock = threading.RLock()
        self.reference = None
        try:
            saved = json.loads(self.path.read_text())
            if (self.boot_id and saved['boot_id'] == self.boot_id
                    and math.isfinite(saved['anchor_utc_s'])
                    and 1577836800 <= saved['anchor_utc_s'] <= 4102444800
                    and 0 <= saved['anchor_monotonic_s'] <= self.monotonic()):
                self.reference = saved
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def timestamp(self):
        with self.lock:
            if self.reference:
                return self.reference['anchor_utc_s'] + self.monotonic() - self.reference['anchor_monotonic_s']
            return self.wall()

    def now(self):
        return datetime.fromtimestamp(self.timestamp(), timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

    def status(self):
        with self.lock:
            return {'ok': True, 'verified': self.reference is not None,
                    'experiment_unix_ms': round(self.timestamp() * 1000),
                    'system_unix_ms': round(self.wall() * 1000),
                    'reference': None if self.reference is None else dict(self.reference)}

    def require_verified(self):
        if self.reference is None:
            raise ClockError('実験時刻を確認してください。管理画面を開くと、この端末の時計に同期します。')

    def sync(self, client_unix_ms):
        if (isinstance(client_unix_ms, bool) or not isinstance(client_unix_ms, (int, float))
                or not math.isfinite(client_unix_ms) or not 1577836800000 <= client_unix_ms <= 4102444800000):
            raise ClockError('端末の日時が不正です。OSの時計を確認してください。')
        with self.lock:
            reference = {'source': 'operator_browser', 'boot_id': self.boot_id,
                         'anchor_utc_s': client_unix_ms / 1000,
                         'anchor_monotonic_s': self.monotonic()}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='clock.', suffix='.tmp', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'w') as handle:
                    json.dump(reference, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            self.reference = reference
            return self.status()
