# What is staged, 2026-09-04

Nothing here is active on the printer. The extra is not symlinked into
`klippy/extras`, there is no `[bedcheck]` section in `printer.cfg`, and
`BED_CHECK.cfg` sits in this directory rather than in `vt_1399/macros/`, so the
`[include macros/*.cfg]` glob does not pick it up. `printer.cfg` on the pi is
byte-identical to its committed version.

All files are untracked. The pi has identical copies under `~/voron`, inert, kept
there only because captures and analysis have to run on the machine. They go stale
the moment anything here is edited.

## Files

| file | what it is |
|---|---|
| `bedcam.py` | machine and camera helper: Moonraker, snapshots, v4l2, park/move, preflight |
| `capture.py` | calibration capture: `exposure`, `sweep`, `probe` |
| `analyze.py` | offline analysis: `noise`, `light`, `compare`, `sheet` |
| `bedcheck.py` | the runtime check: `grab`, `unlock`, `lastjob`, `verdict` |
| `capture_worker.py` | superseded by `bedcheck.py grab`, kept until it is confirmed unused |
| `profiles/trident.json` | measured machine profile. Changed today, see below |
| `profiles/v24.json` | never verified against that machine |
| `BED_CHECK.cfg` | `SAFE_BED_CHECK` macro. Parked out of the include path |
| `../extras/bedcheck.py` | Klipper extra: `BED_CHECK`, `BED_CHECK_PREPARE`, `BED_CHECK_CONFIRM`, `BED_CHECK_DISMISS` |

## Profile changes worth reviewing

`camera_lock` went from three controls to eleven. This is the substantive change of
the day: `gain` was previously left floating, the camera returned to full auto
between runs, and `v4l2-ctl` reports a stale `gain: 8` regardless. Every capture ran
at a different unrecorded gain, which is what made months of numbers incomparable.

- `gain: 24`, calibrated at park fl z045 against the profile's target roi mean of 55
- `power_line_frequency: 1`, 50 Hz. It was 60 on 50 Hz mains, worth 30% on its own
- `default_exposure: 7500`. With gain pinned the exposure saturates here
- `exclude_regions` refitted to where reseat error actually concentrates, now 7.5% of
  the roi. Validated on a held-out reseat: 656 px unmasked, 409 old mask, 148 refit
- `marking_regions` added, the two boxes used to decide plate present or absent

Side effect: the noise floor fell from 2.65 to 1.588 grey levels.

## Things I would want a second opinion on

- The extra shells out and waits on `reactor.pause`. It has been loaded by Klipper
  once and `BED_CHECK_PREPARE` ran clean, but `BED_CHECK` itself has never run live.
- The LED is driven by the extra, not the subprocess, deliberately. If the subprocess
  asked Moonraker to switch it, that request would queue behind the very command
  waiting on it and deadlock. Do not "simplify" that.
- `capture_worker.py` predates the camera fix and does not apply the full lock. It is
  dead code as far as I know; deleting it is probably right.
- The threshold of 200 px is provisional. It has never been tested against a
  reference more than a few minutes old.

## Reverted from the pi

A `[bedcheck]` config section and the `klippy/extras` symlink were added, klipper was
restarted, and both were removed again at your request. `safe_z_home` was NOT
touched; I proposed moving it to the park corner and was stopped before editing, and
that proposal was wrong anyway because the probe cannot read there.
