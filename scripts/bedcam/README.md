# bedcam

Calibration tooling for camera-based bed-clear detection. Captures reference imagery
under controlled pose and lighting, and measures whether there is enough signal to
tell an occupied plate from a clear one.

These run **on the printer**, against Moonraker and the ustreamer snapshot endpoint on
localhost. They need numpy and PIL, which the klippy virtualenv already has:

```bash
~/klippy-env/bin/python ~/voron/scripts/bedcam/capture.py --profile trident exposure
```

`~/printer_data/config` is a symlink to the repo checkout, so `~/voron/scripts/bedcam`
is already present on both machines.

## Profiles

`profiles/trident.json` and `profiles/v24.json` hold everything machine-specific:
snapshot URL, v4l2 device, park positions, sweep heights, light gcode, and the bed
region of interest as fractions of the frame.

The two machines differ in a way that matters. The Trident's bed moves in Z, so the
plate's position in the frame changes with Z and there is a narrow window where it is
visible at all. The 2.4 has a fixed bed and a gantry that moves, so plate geometry is
independent of Z and only occlusion and lighting change.

The v24 profile's camera fields are copied from the Trident and **have not been
verified against that machine**. Check them before trusting a sweep.

## Safety

Every command that moves refuses unless `print_stats.state` is idle and the machine is
already homed. Nothing here ever homes, because homing with something on the plate is
the hazard this project exists to catch.

Moves raise Z before any XY travel and only descend once parked, so there are two
separate numbers. `--clearance` is the Z floor for XY traverses and must clear the
tallest thing on the plate. `--min-z` is the lowest capture height, and it can go below
the clearance because the descent happens at the park, over bare plate. Only lower it
if you know the park is clear. `--dry-run` prints the pose list without moving.

## Workflow

Pick an exposure. Auto-exposure has to be off during capture, otherwise the camera
re-exposes between the lit and unlit frame and the subtraction is meaningless. Reading
`exposure_time_absolute` back while auto is engaged returns the default, not the value
in use, so it has to be laddered:

```bash
capture.py --profile trident exposure
```

Capture the plate with the last print still on it, then clear it and capture again:

```bash
capture.py --profile trident sweep /tmp/occupied --bed occupied --exposure 625 \
    --clearance 40 --min-z 20
capture.py --profile trident sweep /tmp/empty --bed empty --exposure 625
```

The occupied run has to happen before you clear the plate, and it is the more valuable
of the two: with `exclude_object` polygons from the job that just finished you know
where every part is in millimetres, which is ground truth you cannot reconstruct later
without reprinting the same file.

Light levels and sources at a couple of heights:

```bash
capture.py --profile trident probe /tmp/probe --exposure 625
```

Then measure:

```bash
analyze.py noise   /tmp/empty
analyze.py light   /tmp/empty --profile trident
analyze.py compare /tmp/empty /tmp/occupied --profile trident
analyze.py sheet   /tmp/empty --match _c100 --out sheet.jpg
```

`compare` is the one that matters. It reports, per pose, how far an occupied plate sits
from a clear one over the bed region. Read it against the `noise` floor: that ratio is
the headroom any threshold has to work with.

## Measured on vt_1399, 2026-09-03

Exposure 625, 1280x960. Plate is visible below roughly Z70, best at the lowest safe Z,
with a strip at the rear out of frame at every height. Both StealthBurner nozzle LEDs
measured dead, contribution indistinguishable from noise.

Noise floor on a still machine: 2.65 mean absolute, 3.73 RMS, 0.07% of pixels over 20.

Chamber LED contribution over the bed region falls monotonically from +78 at Z20 to
+16 by Z80.

Detection signal against four large parts, ambient-cancelled, at Z20: mean 38.5, with
52% of bed-region pixels over 20 grey levels. The same comparison on raw lit frames
gives 38.2, so ambient drift between runs taken 40 minutes apart contributed under 1%.
With the chamber LED off the signal collapses to mean 3.5 and 4.4% of pixels, so the
LED is what makes parts visible, not just what cancels the room.

Reseating the flex plate is the dominant nuisance term, about thirty times everything
else combined, and it decays with Z because a lower plate fills more of the frame. Two
independent reseat comparisons agreed to two decimal places.

A global pixel fraction is not a good enough metric. Against a single small matte TPU
part it gives only about 4.5:1 over the reseat floor. Largest connected region, after a
3x3 opening, gives roughly 29:1 on the same data, because reseating scatters diffuse
error along edges while an object is one contiguous blob:

```
largest blob (px), cancelled diff > 40    z020    z030    z040    z050
reseat floor                               389     286     366      25
one small TPU part                        7914    8359    5325    1794
four large parts                         64240   36182   22053   22328
```

Operating point is Z30, threshold around 1500 px of largest blob. Z50-60 looked better
on the four-part data and is wrong: a single small part is invisible by Z60.

Detection limit, at blob threshold 20 and Z30:

```
                        blob    regions
no-reseat floor            0          0
reseat floor             741         19
6x6 mm dark part         212          4     not detectable
TPU motor mount         8832          1     detectable, ~12:1
```

With the plate untouched the floor is zero pixels, so a 6 mm dark part would be
detectable. Reseating buries it. The plate, not the camera, sets the lower bound.

Two things that do not work. Registering the reseat away fails because phase correlation
finds zero shift across a reseat: the plate is not translating in the image, most likely
its tilt changes and the specular highlights move, which is not an image transform.
Masking specular pixels fails because the detection mechanism *is* brightness, a part
lit by the chamber LED against a plate that reflects it away, so a brightness mask
punches holes through the signal and fragments the blob faster than it suppresses the
floor.

## Runtime check

`bedcheck.py` is the print-time check, and `extras/bedcheck.py` is the Klipper side
that drives it. The split matters: the extra owns the chamber LED and does its own
waiting on the reactor, while the subprocess only ever touches the camera. If the
subprocess asked Moonraker to switch the LED it would deadlock, because `BED_CHECK`
is holding the gcode queue that Moonraker would have to queue behind.

CLI, all of which run standalone:

```bash
bedcheck.py --profile trident grab --out /tmp/lit.jpg --lock
bedcheck.py --profile trident unlock
bedcheck.py --profile trident lastjob
bedcheck.py --profile trident verdict --reference ~/bedcam-data/ref1 \
    --lit /tmp/lit.jpg --unlit /tmp/unlit.jpg --z 25 --park fl
```

`verdict` exits 0 for CLEAR, 1 for OCCUPIED or NO_PLATE, 2 if it could not run. It
decides NO_PLATE from the marking regions rather than from blob size, because a
large print exceeds the missing-plate blob figure and the two would be confused.

### Enabling it

Not enabled yet. It needs a klipper service restart, which `RESTART` does not do.

```bash
ln -s ~/voron/extras/bedcheck.py ~/klipper/klippy/extras/bedcheck.py
```

Then in `printer.cfg`:

```ini
[include macros/BED_CHECK.cfg]

[bedcheck]
reference: ~/bedcam-data/ref1
park: fl
check_z: 25
threshold: 200
marking_threshold: 0.5
```

```bash
sudo systemctl restart klipper
```

Both halves have to land together. A `[bedcheck]` section without the symlink makes
the next restart fail with an unknown config object.

### Using it

`SAFE_BED_CHECK` is the entry point. It asks Moonraker how tall the last print was,
lowers the bed by that plus a margin, parks at the front left, brings the bed up to
the camera pose and runs `BED_CHECK`. It refuses outright if the machine is unhomed,
because Z is then unknown and the bed cannot be moved clear of anything on it, and
it refuses to descend to the camera pose if the last print was taller than that pose.

`BED_CHECK REQUIRE_CLEAR=0` reports without raising, which is the safe way to try it.

It is deliberately NOT wired into `PRINT_START`. Prove it by hand first.

### Operating point

Park fl, Z25, exposure 7500, gain 24. Measured 2026-09-04 against reference ref1:
untouched floor 0 px, reseat floor 25, smallest part 1241, missing plate 6095.
Threshold 200 sits 8x above the reseat floor and 6x below the weakest part seen.

Every capture before 2026-09-04 evening predates the camera-gain fix and is not a
valid reference. See INDEX.md in the dataset directory.

### The prompt, and how the reference stays current

A reference is a hint, not a rule, and it goes stale. Rather than re-shooting one by
hand, the check asks. After every `BED_CHECK` with `TRAIN=1` (the default) the frames
and the verdict are written to `~/bedcam-data/train/<timestamp>/` and a Mainsail
dialog appears reporting what the camera thinks and asking what is actually there:

```
Bed is clear     -> BED_CHECK_CONFIRM ANSWER=clear
Parts on bed     -> BED_CHECK_CONFIRM ANSWER=occupied
No build plate   -> BED_CHECK_CONFIRM ANSWER=no_plate
Skip             -> BED_CHECK_DISMISS
```

Answering writes `label.json` next to the frames, which is the training set. Answering
`clear` also promotes that capture to `~/bedcam-data/reference/`, so the reference is
refreshed at exactly the moment a human has confirmed the plate is good. That is the
only thing that ever writes the reference.

When the answer disagrees with the verdict it is logged as a miss, with the blob and
marking figures, so false positives and false negatives are both recoverable from the
training directory later.

Dismissing records nothing. A capture is never labelled by guessing, and a dialog
closed by the X or by a restart simply leaves that capture unlabelled. Only one
capture is ever pending, so a stale dialog cannot mislabel a later check.

`~/bedcam-data/ref1` is left alone as the original measured dataset. The live
reference is a separate directory holding only the z025 pair plus `reference.json`
recording where it came from and when.
