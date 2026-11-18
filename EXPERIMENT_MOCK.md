# Aria experiment modes and Pi setup

The experiment pages support two output modes. Mock is selected by default and does not call the servo or LCD services. Hardware mode is enabled per session in the admin page and uses the existing `LiveController` lease and the calibrated Pi endpoints.

## MacBook and Pi over Ethernet

The MacBook opens the Pi's web UI at `http://<Pi Ethernet IP>:8767/experiment/admin`. On the Pi, the experiment server sends servo commands to `127.0.0.1:8768` and iris color changes to `127.0.0.1:8769`. The MacBook does not need access to those control ports.

`project-aria-servo-legacy-ui.service` currently listens on `0.0.0.0:8767`, which means every Pi network interface. Keep a tokenless setup on a dedicated direct link, or restrict the service to the Ethernet interface/firewall before using it on a shared network. No external API account or API key is involved. `ARIA_EXPERIMENT_ADMIN_TOKEN` is optional; if an operator configures it, the admin page asks for it under “接続設定”.

## Settings and sessions

The current preset configuration is stored in the Pi's experiment data file, together with sessions. The `/study` page reads it at startup and saves slider changes back to the Pi. Creating a session freezes a copy of that configuration and its version hash; later adjustments do not change existing sessions.

The 42 slots remain separate presentation trials. `physical_state_id` identifies slots with identical visible settings. `intended_emotion` is the source preset group, not a correct answer; the page collects subjective emotion responses and does not score correctness. Analyze repeated physical states as the same stimulus using `physical_state_id`.

## Hardware behavior

The admin page can check Pi service connectivity without sending a pose. Creating a hardware session does not move the robot. Servo/LCD commands begin only after the participant records consent and starts a trial. The participant browser sends a heartbeat while the session page is active; the existing lease expires and returns the eye pose to its safe center if the page disconnects. Abort, completion, and page exit also release the lease.

The UI stores the eyelid input exactly as shown in the adjustment page. The current interface convention uses 0 for the open endpoint and 100 for the closed endpoint; the hardware adapter converts this value to normalized openness without changing the saved setting. Horizontal and vertical gaze values are normalized from the existing calibrated ±20° range. Servo limits and calibration are not edited by the experiment code.

Each hardware event records the command request time and the control service's acceptance time. The hardware is open-loop and does not report physical eye position, so these timestamps do not prove the eyes reached a target pose. Verify movement and timing in a pilot before using the system for a study.

## Pi service data path

The systemd unit stores `sessions.json` under `/var/lib/project-aria-experiment`. The service can run the admin API without an authentication token on an isolated direct link. If a token is configured, admin configuration, session, and export routes require it; participant links continue to use their per-session access token in the URL fragment.

## Checks

```sh
python3 -m unittest discover -s tests -p 'test_experiment*.py' -v
python3 -m unittest discover -s tests -p 'test_legacy_ui.py' -v
python3 -m unittest discover -s tests -p 'test_servo_eye_only.py' -v
```

These checks use mocks/stubs and do not actuate hardware. Physical direction, eyelid mapping, command timing, and Ethernet-only reachability still require a pilot on the Pi.
