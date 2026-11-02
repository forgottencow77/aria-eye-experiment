# Aria Eye UI and Emotion Experiment

This repository contains the existing manual eye controls and an experiment workflow with mock and hardware presentation modes.

## Pages

- `/` — existing servo controls
- `/simple` — simple manual controls
- `/study` — emotion presets and manual adjustment
- `/experiment/admin` — review Pi-saved presets, select mock or hardware output, and create sessions
- `/experiment/participant` — consent, practice, presentation, and response pages

The experiment settings are saved on the controller host and shared across browsers. A session stores a snapshot of its settings and presentation order. The participant page contains no drawn eyes.

## MacBook to Pi

For a direct Ethernet connection, open the Pi's port 8767 from the MacBook browser:

```text
http://<Pi Ethernet IP>:8767/experiment/admin
```

The MacBook talks to the experiment UI on the Pi. The Pi then calls its local servo service on `127.0.0.1:8768` and iris LCD service on `127.0.0.1:8769`; the MacBook does not need direct access to those ports.

If the Pi-hosted experiment UI on port 8767 is unavailable, the controller can run on the MacBook while sending commands to the Pi's servo and LCD services:

```sh
ARIA_SERVO_API_BASE=http://raspberrypi.local:8768 \
ARIA_LCD_API_BASE=http://raspberrypi.local:8769 \
python3 legacy_ui_server.py --host 127.0.0.1 --port 18867
```

Then open `http://127.0.0.1:18867/experiment/admin`. The MacBook controller stays bound to loopback and keeps the live-control lease locally. This requires network access to the Pi's ports 8768 and 8769; restrict those ports to a trusted link or use direct Ethernet.

Configured API hostnames are resolved to IPv4 when the controller starts. This avoids the slow unreachable IPv6 path observed for `.local` on some Wi-Fi connections. Restart the MacBook controller after the Pi changes networks or its address changes.

The systemd unit currently binds the web UI to all Pi interfaces. Use a dedicated direct link or restrict the bind address/firewall before connecting the Pi to a shared network. No external API account or API key is used. `ARIA_EXPERIMENT_ADMIN_TOKEN` is optional; if configured manually, enter it in the collapsed connection settings.

## Presentation modes

Mock mode is the default and sends no actuator commands. Hardware mode first checks that the Pi servo and LCD services respond, then sends poses through the existing live-control lease after the participant consents and starts the first presentation. The browser keeps the lease alive during presentation; disconnect, abort, and session completion release it. Existing servo calibration and channel limits remain in force.

Hardware heartbeat monitoring starts after preparing B₀ during the instruction phase and treats `active: false` as a lost lease.
The server also checks lease ownership at subsequent presentation phases; it does not silently
reacquire control midway through a presentation. A failed attempt is marked invalid and requires
explicit replay before an answer can be accepted. Heartbeats continue during answers and between trials to hold B₀. The experiment-specific
stop and lease-expiry pose is also B₀; the Pi receives the same fallback pose for loss
of upstream communication. Manual control retains its existing centered/open stop behavior.
Loss of control while answering does not invalidate an already completed presentation,
and the next presentation reacquires control and prepares B₀. If the entire Mac-to-Pi
link is unavailable, Pi can restore the fallback servo pose but the separate LCD service
cannot receive the gray-color restoration command until communication is available.

Hardware events record command request and controller acceptance times. The servo system has no position feedback, so the saved data does not claim that the physical eyes reached a pose at a particular time. Test hardware mode with a pilot session before using it for participants.

The study page saves manual emotion settings to the Pi. The values remain in the same UI convention used for adjustment; conversion to normalized servo openness happens only when a hardware command is sent.

## Presentation sequence (version 2)

Prepare B₀ at the first presentation or after reacquiring control. Then each trial
uses **2 s instruction at B₀ → 0.5 s transition → 5 s hold → 0.5 s return to B₀ → answer**.
There is no separate 0.5 s baseline phase. B₀ remains held while answering and waiting
for the next trial. The `baseline` event remains a zero-wait pre-transition checkpoint
for log compatibility. The nominal total is 8 s plus communication/storage overhead.
These are software wait durations, not measured mechanical arrival times.

New sessions snapshot `presentation_protocol`, included in JSON and CSV exports.
Old completed data are not rewritten. Old sessions with superseded settings/protocol
cannot newly start; create a new session. A participant page using the old protocol
must reload before a new trial can begin.

Configuration saves require the version read by the browser. A stale or missing
version is rejected; session creation also rejects settings different from the saved
configuration and never changes that configuration. The admin page shows B₀ above
Create and in the session list. Explicit JSON edits must be saved before creating a
session. This prevents an old 65% page from overwriting the confirmed 40% baseline.

## Inspect all 42 conditions

Open `/study`, then choose **42条件の確認** in the display-mode selector.
The page takes a snapshot of the server-saved configuration and uses the same
condition generator as the experiment. Choose an emotion and C/L/G combination,
or use **前へ / 次へ** to walk through all 42 slots in order.

B₀ currently uses gray `#BFBFBF`, eyelid UI value **40%**, and gaze UI values
0° horizontal / −8° vertical (updated at the user’s request on 2026-11-02).
The six emotion settings are unchanged. Existing experiment sessions retain their
original frozen configuration; newly created sessions use the updated baseline.

ON uses that emotion's value; OFF uses the corresponding value from B₀, not zero
or a disabled servo. The page shows the effective color, eyelid and gaze values,
the current slot number, and other slots with identical settings. Adjustment
controls are hidden in this mode, and reviewing conditions does not save presets
or create participant responses.

Press **実機操作を開始** to send the selected condition. While active, changing the
selection sends the next condition and holds it until another selection or stop.
**B₀を実機で表示** presents and holds the frozen baseline (including its color, eyelid and gaze).
**選択中の条件を表示** returns to the selected stimulus without advancing the slot.
These buttons explicitly start hardware control if it is not already active.
**正面・開眼へ戻す** ends control using the existing stop behavior. Switching modes
also ends control. This is a manual inspection tool, not the timed 0.5/3-second
experiment sequence. To refresh the configuration snapshot, switch back to the
adjustment mode and enter inspection mode again.

## Calibrated preset coordinates

The emotion JSON retains the UI numbers used during the original tuning session.
The `eyelid_open_percent` name is historical: 0 means the photographed open
endpoint and 100 the closed endpoint. Vertical values also retain the old UI
coordinate convention, not a new physical-angle calibration. Do not negate these
values or change emotion presets to compensate for controller updates.

The end-to-end regression in `tests/test_live_control.py` fixes the original
CH0–CH5 pulse targets for all six emotions and B₀. The current study/experiment
adapter sends `openness = 1 - eyelid_open_percent / 100`; LiveController sends
`blink = 1 - openness` and preserves the historical gaze sign. The matching Pi
runtime uses open CH4/CH5 = 850/2200 µs and closed = 2000/1000 µs.
Deploy this Pi runtime together with the controller's `live_control.py` and
`limits.json`. Mixing the old Pi polarity with the current adapter reverses the
calibrated eyelid appearance. Stop/disconnect use the same open endpoint mapping.
All existing pulse bounds are retained. Automated tests verify commanded pulse
values; they do not measure physical servo position or validate emotion recognition.

## Run locally

```sh
ARIA_EXPERIMENT_DATA_DIR=/tmp/aria-experiment-data \
python3 legacy_ui_server.py --host 127.0.0.1 --port 8767
```

Open `http://127.0.0.1:8767/experiment/admin`. Mock mode works without a Pi. Hardware mode requires the server to run on the Pi with both local control services available.

## Tests

```sh
python3 -m unittest discover -s tests -p 'test_experiment*.py' -v
python3 -m unittest discover -s tests -p 'test_legacy_ui.py' -v
python3 -m unittest discover -s tests -p 'test_servo_eye_only.py' -v
```

The experiment tests use a controller stub and do not move the robot.
