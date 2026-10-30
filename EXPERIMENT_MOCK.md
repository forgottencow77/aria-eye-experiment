# Aria experiment mock mode

The experiment pages add a mock-only workflow alongside the existing manual pages. The experiment engine has no servo or LCD controller dependency. It stores sessions atomically as JSON and exports one CSV row per main trial.

## Pages

- Admin: `http://raspberrypi.local:8767/experiment/admin`
- Participant: open the one-time link generated for a session in the admin page
- Existing manual pages remain at `/`, `/simple`, and `/study`

The admin API requires `X-Aria-Admin-Token`. The token is read from `ARIA_EXPERIMENT_ADMIN_TOKEN`; it is never embedded in the page. On the Pi, create `/etc/project-aria-experiment/admin.env` with one line:

```text
ARIA_EXPERIMENT_ADMIN_TOKEN=<a long random secret>
```

Restrict that file to the service administrator (mode `0600`), then restart `project-aria-servo-legacy-ui.service`. The unit creates `/var/lib/project-aria-experiment` as its persistent session directory. On a local development server, set `ARIA_EXPERIMENT_ADMIN_TOKEN` and optionally `ARIA_EXPERIMENT_DATA_DIR` before starting `legacy_ui_server.py`.

## Workflow and limits

The admin page loads the latest confirmed defaults and, when present, the current browser's `aria-emotion-settings-v2` values. A session snapshots those settings and records a configuration hash. Its 42 slots are randomized with a stored seed; consecutive repeats of a physical state and runs of three identical intended emotions are rejected. Identical physical states remain separate trial slots and are listed in the admin view.

The participant page records consent, one excluded practice response, 42 main responses, optional feedback, and breaks after trials 14 and 28. Each presentation uses a 1.0-second instruction, then baseline 0.5 seconds, transition 0.5 seconds, target hold 3.0 seconds, and baseline return 0.5 seconds. A reload during a presentation requires an explicit replay and increments the attempt count. Aborting preserves the session and marks an active main trial invalid.

All visual movement is rendered in the browser. No experiment route calls `/api/live/*`, the servo service, or the LCD service. The admin page therefore reports real-device status and calibration as unverified. Hardware preview, inter-process control locking, and real-device timing verification are intentionally not implemented in this mock phase.

The participant link contains an unguessable access token in its URL fragment, which browsers do not send in HTTP requests or access logs. The page sends it in the body of same-origin API requests. Admin list, session, configuration, and export APIs require the separate admin token. Do not share admin tokens or participant links publicly.

## Checks

```sh
python3 -m unittest discover -s tests -p 'test_experiment*.py' -v
python3 -m unittest discover -s tests -p 'test_legacy_ui.py' -v
python3 -m unittest discover -s tests -p 'test_servo_eye_only.py' -v
```

The experiment tests exercise the 42-slot engine, persistence, participant flow, randomized constraints, and HTTP separation using a controller stub. They do not move the robot. The existing `test_live_control.py` currently has 10 assertions that disagree with the deployed eyelid and vertical direction mapping; the experiment change leaves that control mapping untouched.
