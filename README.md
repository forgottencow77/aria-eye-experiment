# Aria Eye UI and Emotion Experiment

This repository contains the existing manual eye controls and an experiment workflow with mock and hardware presentation modes.

## Pages

- `/` — existing servo controls
- `/simple` — simple manual controls
- `/study` — emotion presets and manual adjustment
- `/experiment/admin` — review Pi-saved presets, select mock or hardware output, and create sessions
- `/experiment/participant` — consent, practice, presentation, and response pages

The experiment settings are saved on the Pi and shared across browsers. A session stores a snapshot of its settings and presentation order. The participant page contains no drawn eyes.

## MacBook to Pi

For a direct Ethernet connection, open the Pi's port 8767 from the MacBook browser:

```text
http://<Pi Ethernet IP>:8767/experiment/admin
```

The MacBook talks to the experiment UI on the Pi. The Pi then calls its local servo service on `127.0.0.1:8768` and iris LCD service on `127.0.0.1:8769`; the MacBook does not need direct access to those ports.

The systemd unit currently binds the web UI to all Pi interfaces. Use a dedicated direct link or restrict the bind address/firewall before connecting the Pi to a shared network. No external API account or API key is used. `ARIA_EXPERIMENT_ADMIN_TOKEN` is optional; if configured manually, enter it in the collapsed connection settings.

## Presentation modes

Mock mode is the default and sends no actuator commands. Hardware mode first checks that the Pi servo and LCD services respond, then sends poses through the existing live-control lease after the participant consents and starts the first presentation. The browser keeps the lease alive during presentation; disconnect, abort, and session completion release it. Existing servo calibration and channel limits remain in force.

Hardware events record command request and controller acceptance times. The servo system has no position feedback, so the saved data does not claim that the physical eyes reached a pose at a particular time. Test hardware mode with a pilot session before using it for participants.

The study page saves manual emotion settings to the Pi. The values remain in the same UI convention used for adjustment; conversion to normalized servo openness happens only when a hardware command is sent.

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
