# Bringing the bed into view safely

The problem this has to solve: after a restart or power cycle the machine is unhomed,
the bed may hold the previous print, and the check that would tell us so cannot run
until the machine has already moved.

## Measured constraints

1. The gantry is fixed in Z and the bed moves. Increasing Z lowers the bed away from
   the nozzle. Lowering the bed is the safe direction; raising it is the dangerous one.
2. An XY traverse collides with anything on the plate taller than the current
   nozzle-to-plate gap. That gap has been observed at 2 mm between operations.
3. The plate is visible to the camera below about Z70 and best at Z20-30.
4. With the toolhead parked mid-bed it fills the frame and no plate is visible.
   Measured: the best marking correlation across the entire reference ladder is 0.214
   against a plate-present threshold of 0.5. Nothing can be inferred in that state.
5. Z homes through `probe:z_virtual_endstop` with `safe_z_home` at 150,150, so a Z
   home probes the centre of the bed, which is where parts are.
6. The probe cannot read the bed from the park position.
7. Probe offset is x 0, y +16.

Together: to look, the head must move. To move the head, XY must home. To place the
bed at the camera pose, Z must home. To home Z is to probe the middle of the bed.

## The asymmetry the method rests on

Moving the bed **away** from the gantry fails benignly. The worst case is running out
of travel and stalling the Z motors against the bottom, which loses sync on a machine
that is unhomed anyway and is recovered by the subsequent home and `Z_TILT_ADJUST`.

Moving the bed **toward** the gantry, or sweeping the head in XY, fails destructively.

So every blind move must be in the safe direction, and every move in the dangerous
direction must be either guarded by a sensor or confined to a volume we have already
looked at.

## Proposed sequence

**1. Open clearance, blind but in the safe direction.**
With Z unhomed, lower the bed using `FORCE_MOVE` on the Z steppers, or
`SET_KINEMATIC_POSITION Z=0` followed by a normal move to `Z=d`.

Choose `d` from `object_height` of the last job, which Moonraker already reports and
`BED_CHECK_PREPARE` already fetches, plus a margin. Because the starting gap cannot
be negative, lowering by `d` guarantees a gap of at least `d` regardless of where the
bed started. That is a real guarantee, not an estimate, and it is the whole reason to
bother with the last-job data.

It does not cover something taller than the last print, a dropped tool or a part
knocked over. It also fails if the bed runs out of travel first, though that failure
leaves the bed at the bottom, which is the safest state for the next step.

**2. Home XY.**
This is the one step that cannot be guarded, because the camera cannot see past the
head until it has happened. Step 1 is what bounds its risk.

**3. Park at the front left and look.**
Capture a lit/unlit pair. Locate the plate markings and match them against the
reference ladder to infer the bed height. If no height matches, the verdict is
"cannot see the bed", and the correct response is to stop and ask a human rather than
to guess.

**4. Coarse verdict at whatever height the bed is.**
If the bed is in view but low, a check is still possible, just less sensitive. At
z050 the floor is 0 px, a missing plate reads 295 px and parts read 835 px. That is
enough to catch gross occupancy before anything else moves, and it costs nothing.

**5. Raise to the camera pose, with the camera as the endstop.**
This is the only move in the dangerous direction, and it is confined to the column
above the park corner because the head stays parked there. Raise in small increments,
capture between them, and stop when the marking row matches the z025 reference. The
stop condition is a match rather than a switch, but the structure is a homing move:
approach in bounded steps until a sensor says stop, and abort if it never does.

Before the first increment, confirm from the frame that the park corner itself is
clear. That is the only volume being swept.

**6. The real check at z025**, against the reference, as already implemented.

**7. Only if clear**, home Z, `Z_TILT_ADJUST`, mesh, touch. By this point the bed has
been looked at, so probing the centre is no longer blind.

## What is not known yet

- **Where the head sits after `G28 X Y`.** Endstops are at X300 Y307, the rear right.
  If that is outside the camera's view of the bed, step 3 could happen before moving
  to the park, and the park move itself becomes guarded. Untested.
- **How tight the bed height has to be** for the reference comparison to hold. The
  reference ladder is in 5 mm steps and the check needs far better than that. A fine
  sweep at z024 to z026 in 0.5 mm steps would give the tolerance, and that tolerance
  decides whether step 5 can converge at all.
- **Whether the marking row resolves bed height** to better than half a millimetre.
  The slope is 2.245 rows per mm, so half a millimetre is about one pixel row. This
  needs sub-pixel matching and has not been demonstrated.
- **Whether `force_move` is enabled**, and how much Z travel is actually available
  from a typical post-print position.
- **Whether stalling the Z motors at the bottom is acceptable.** The method tolerates
  it, but that is a judgement about the hardware, not about the software.

## What this deliberately does not do

It does not move `safe_z_home`. An earlier proposal to home Z at the park corner is
dead: the probe cannot read there.

It does not home Z before looking, under any circumstances.

It does not ask a human to authorise a full `G28`, because that homes Z and Z homing
is the collision being avoided. If a human is asked anything, it is to authorise the
XY home in step 2, which is the actual unguarded risk.
