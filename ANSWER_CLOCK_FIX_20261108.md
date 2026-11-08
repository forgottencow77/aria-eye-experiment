# Answer identity and offline clock fix — 2026-11-08

This change addresses acceptance findings D3 (late answer misattribution) and D2
(clock skew), corresponding to the two fixes approved by the experimenter.

## Changes

- A new opaque presentation ID is generated for each trial/attempt, including
  practice and replay. Answers must match the current ID. The ID is restored
  after answer-page reload and recorded in new responses and exports.
- The admin page compares experiment UTC to the operator computer clock and
  initializes/synchronizes it before creating sessions. No internet is required.
- Experiment UTC advances using monotonic elapsed time and is independent of
  subsequent OS clock jumps. Same-boot server restarts restore the reference;
  a new boot requires another clock check. Synchronization of an already verified
  clock is rejected during running experiments or manual control.
- The experiment clock is used consistently for session, event, answer, and
  hardware-command timestamps. Its reference is exported for new sessions.
- The Pi OS clock was corrected at deployment. The browser-based mechanism
  corrects experiment UTC, not OS/system-log time on future offline boots.

## Verification

- 72 automated Python/Node.js tests passed, including previous-trial/replay ID
  rejection, unchanged state after rejected answers, same-attempt reload recovery,
  monotonic time under OS clock changes, same/new boot handling, and sync rejection
  during active sessions.
- The deployed Pi completed `SELFTEST_ANSWER_CLOCK_20261108`: practice plus 42
  mock answers, with one deliberate replay. This was an accelerated API run.
- 87 missing/stale answer requests were rejected. CSV and JSON presentation IDs
  and clock references agree. New records use 2026-11-08 UTC.
- Experiment clock difference to the Mac was approximately 3 ms at the check;
  the corrected OS clock was within one second. These are local synchronization
  observations, not precision clock guarantees.
- All five pre-existing session objects, including completed P000, and the current
  configuration match their pre-deployment copies exactly.
- The five deployed changed files match local SHA-256 hashes. Only the experiment
  UI service was restarted. No hardware presentation was actuated for this change.

Private test exports and backups are in
`experiment_data/answer-clock-fix-20261108/` (ignored by Git).
Pi backup: `/home/forgottencow/eye-display/legacy-ui/releases/20261108-answer-clock/previous`.

## Remaining scope

Acceptance findings D4 (abort when hardware stop fails) and D5 (study cached
manual values after config-load failure) were explicitly excluded by the user and
remain open. The power and physical-timing findings and the LCD version-management
gap also remain unresolved. This fix does not change the confirmed emotion/B₀
settings, calibration, trial count, or presentation durations, and is not a new
GO decision for the full experiment.
