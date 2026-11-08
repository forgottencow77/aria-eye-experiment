# Preset mapping verification — 2026-11-02

The original tuning pipeline (`d1b4d3d`) passed the saved lid fraction as
normalized openness to the old Pi eyelid mapping. Changing that adapter without
deploying the new Pi mapping inverted the same saved settings. The later vertical
sign correction also changed the physical targets of the historically tuned gaze values.

This restoration keeps all six presets and B₀ unchanged, deploys the eyelid
mapping from `6f4d3e4`, and restores the historical positive gaze sign.
It does not establish that the historical UI direction labels are physically correct.

## Verification

- Full automated suite: 53 tests passed.
- Regression compares all seven preset CH0–CH5 targets to the original pipeline.
- The Pi servo runtime was backed up and updated; its loaded eyelid homes are 850/2200 µs.
- Restart preserved all existing PWM values, enabled outputs, and iris color.
- The Mac controller was restarted with matching limits and direction mapping.
- Each preset was sent through the running Mac controller to the Pi. Reported PWM values matched the table below.
- Eyebrow values and enabled states stayed unchanged. The original iris color was restored after the test.
- Stop returned CH0–CH5 to 1600,1450,1600,1550,850,2200 µs.
- Persisted preset configuration still equals the confirmed defaults.

| Preset | CH0–CH5 (µs) | Command response (ms) | Reported exact target observed after (ms) |
| --- | --- | ---: | ---: |
| anger | 1600, 1450, 1600, 1550, 1482, 1540 | 30.3 | 448.8 |
| disgust | 1700, 2125, 1960, 1450, 1482, 1540 | 32.6 | 676.9 |
| fear | 1500, 2200, 2000, 1650, 1770, 1240 | 34.9 | 677.7 |
| joy | 1700, 1450, 1600, 1450, 1206, 1828 | 30.5 | 758.4 |
| sadness | 1500, 1450, 1600, 1650, 1770, 1240 | 32.2 | 671.1 |
| surprise | 1600, 1450, 1600, 1550, 850, 2200 | 34.3 | 521.3 |
| neutral | 1640, 1450, 1600, 1510, 1598, 1420 | 34.2 | 522.1 |

The last column includes request overhead and approximately 100 ms polling.
These are software-reported PWM values, not encoder readings or a video-based
measurement of physical position. In this run exact reported equality was observed
after 449–758 ms; this does **not** validate that every physical transition finishes
within the experiment’s 0.5-second interval. LCD optical update time and emotion
appearance were not measured.

Deployment backup on Pi:
`/home/forgottencow/eye-display/legacy-ui/releases/20261102-preset-restore/previous`.
The previously inactive Pi legacy UI remains inactive; the active controller is
the Mac service on `http://127.0.0.1:18868`.

## Subsequent baseline setting update

After the verification above, the user changed only B₀ eyelid UI value from 65%
to 40%. Its current CH4/CH5 targets are 1310/1720 µs. The table above remains a
historical measurement at 65%, not a measurement of the new baseline. The six
emotion presets and servo mapping are unchanged.
