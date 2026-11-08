# Observed hardware self-test — 2026-11-08

## Result

**PASS for this end-to-end hardware/API run:** practice plus all 42 main conditions
completed, including `joy__CLG` at slot 35. No API/presentation errors or replays
were recorded. The control lease was released and the final reported pose/color
was B₀: gray `#BFBFBF`, eyelid UI value 40%, gaze X=0 / Y=−8.

The experimenter explicitly requested the run and stated they would observe the
robot. This report does not substitute software-reported PWM positions for a
physical measurement. The experimenter reported after the run: 「止まらず、見た目も問題なさそう」
(no stopping observed; appearance seemed acceptable). This is qualitative visual
confirmation, not a frame-timed measurement of mechanical or optical response.

## Method

- Code under test: `8979f89d71e6e498df0afc25d3410b99c6463b4d` (answer-ID and clock fixes).
- Pi-hosted experiment API, `pilot` / `hardware`, confirmed frozen configuration
  `8356e0923050`, random seed 20261108.
- Test participant: `SELFTEST_HARDWARE_20261108_OBSERVED`.
- Session: `aria_GaTezjuG8W02sBbh`, final status `completed`, 42/42 main responses.
- The operator-authorized runner called the real experiment APIs in protocol-v2
  order: instruction 2 s, transition 0.5 s, target hold 5 s, return 0.5 s.
- Each answer screen period was held for an additional 0.5 s before the runner
  submitted a synthetic answer. Breaks after trials 14 and 28 held B₀ for 5 s
  before confirmation. These are permitted untimed answer/break periods.
- Every answer is **synthetic**: `other`, confidence `null`, response latency `0`.
  Exclude this SELFTEST from participant emotion-recognition analysis.
- The robot was actuated through the experiment controller. The browser's full
  button/counter flow was not automated; this is a real-time API-driven test.
- A heartbeat maintained the lease. Read-only sampling monitored servo state and
  Pi power flags. The runner was configured to stop on active undervoltage/
  throttling, communication/lease loss, reboot, or failure to reach a PWM target
  during the hold. No stop condition occurred.

## Evidence

| Check | Result |
| --- | --- |
| Practice and all 42 main trials | Completed |
| Experimenter visual observation | No stopping; appearance seemed acceptable |
| Target values in accepted commands | All match generated conditions |
| Reported six-axis PWM targets | Matched for all 43 presentations |
| Target PWM equality observed by hold start | All 43 |
| Maximum observed equality time after API acceptance | 492.5 ms |
| API/presentation errors | 0 |
| Reboots during monitoring | 0 |
| Active undervoltage/throttling flags | 0 of 1,388 samples |
| New kernel undervoltage entries during this run | 0 |
| Prior configuration and session records, including P000 | Unchanged |
| JSON/CSV answer presentation IDs | Consistent for all 42 rows |
| Final live-control lease | Inactive |
| Final baseline pose/color | B₀ |

For the six color-only conditions the servo pose was already at its target before
command acknowledgement. Their raw logged relative equality time can therefore
be negative; it is not a negative movement duration. State sampling is about
100 ms apart and includes request overhead. The measurements describe **PWM
values reported by the controller**, not mechanical settling or LCD optical delay.

Power monitoring covered Pi uptime 2021.21–2441.24 s (about seven minutes), all on
boot `9f23b15a-ef20-4f9a-881d-375b642e3ab1`. The flag remained `0x50000`, representing
historical undervoltage/throttling, with no current fault bits during sampling.
The three kernel undervoltage events remained at uptime ~164.58, 172.64 and
178.69 s, all before this test. Their history has not been cleared.

## Boundaries

This successful run improves the evidence for the current hardware setup. It does
not independently establish physical completion within 0.5 seconds, the optical
color-change timing, or long-term power reliability. The two excluded abnormal-path
software findings (abort on hardware-stop failure and stale manual cache after
config-read failure) remain open. No stimulus, calibration or protocol change was
made for this test.

Private raw monitoring logs, exports and the runner are in
`experiment_data/hardware-selftest-20261108/` (Git-ignored). JSON and CSV are also
available from the Pi admin page under the SELFTEST participant above.
