# Baseline configuration and presentation flow correction

## Findings on 2026-11-02

The completed P000 session contains 42 answers and a frozen B₀ eyelid value of
65%. At investigation time the controller's current saved configuration also
contained 65%, despite the earlier explicit update to 40%.

`ExperimentStore.create()` previously copied the submitted session configuration
back into `current_config`. The admin page submitted its previously loaded JSON,
so an old 65% page could both create a 65% session and overwrite the saved 40%
baseline. No request audit was available to attribute the exact browser action.
The saved values and the overwrite path were directly verified; this was not
merely a display-cache issue.

## Configuration correction

- Current saved B₀ is restored to 40%; its color and gaze, and all six emotion
  presets, remain unchanged.
- Creating a session never updates the common saved configuration.
- HTTP session creation rejects settings that differ from the current saved ones.
- Configuration writes require the version read by the client. Missing/stale
  versions are rejected, including writes from old study/admin pages.
- B₀ is visible above Create, in the session list, and on the participant consent
  screen. Explicit admin JSON edits require Save before session creation.
- Previously completed sessions and all responses are preserved exactly. The
  P000 result remains a 65% experiment, not relabeled as 40%.

## Presentation version 2

Prepare B₀ on first acquisition/replay recovery; subsequent trials retain B₀:

1. Instruction at B₀: 2 seconds.
2. Transition command, then 0.5 seconds.
3. Target hold: 5 seconds.
4. Return to B₀, then 0.5 seconds.
5. Answer and next-trial waiting: keep B₀.

There is no additional baseline wait. The `baseline` log event is retained as a
pre-transition checkpoint. First acquisition may move to B₀ during instruction.
The nominal sequence takes 8 seconds plus communication/storage overhead. Exact
physical completion within 0.5 seconds remains unverified.

Heartbeats remain active during answers and next-trial waiting. Experiment stop
and lease expiry send the session B₀ instead of the manual centered/open pose.
Every experiment servo frame carries the bounded B₀ fallback pose to the Pi;
loss of upstream updates also returns the servo outputs to B₀. Manual behavior
and calibrated pulse bounds are unchanged. If the entire upstream connection is
unavailable, Pi can restore the servo pose but the separate LCD color restoration
cannot be guaranteed until communication returns.

New sessions store/export the protocol version and timing. A stale participant
page cannot begin a new trial without the version-2 handshake. Superseded unused
sessions require creation of a fresh session.

## Verification and deployment

- 64 automated tests passed, including the complete mock experiment, all 42
  inspection payloads, new timing sequence, answer-period heartbeats, B₀ stop and
  stale fallback, historical pulse targets, stale settings rejection and replay.
- Running HTTP APIs rejected attempted old-65 saves/session creation and returned
  the saved 40% baseline (configuration version `8356e0923050`).
- Pi and Mac services were updated. Pi restart preserved existing PWM outputs,
  enabled states and iris color; no experimental trial was actuated in this update.
- The existing sessions were compared to the pre-change backup and are unchanged.
- A new hardware pilot session, SELFTEST40_V2, was created with B₀=40 and protocol
  version 2. It is awaiting consent; creation did not move the robot.
- Full physical timing, optical color synchronization and the updated 42-trial
  hardware run remain to be verified. Browser visual testing was not performed.

Pi deployment backup:
`/home/forgottencow/eye-display/legacy-ui/releases/20261102-flow-v2/previous`.
