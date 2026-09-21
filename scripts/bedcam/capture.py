import argparse
import sys
import time

import bedcam


def light_tag(lights):
    return "-".join("%s%03.0f" % (k[0], v * 100) for k, v in sorted(lights.items()))


def apply_lights(m, lights):
    for name, level in sorted(lights.items()):
        m.set_light(name, level)


def cmd_exposure(m, args):
    m.preflight()
    for g in m.p.get("pre_gcode", []):
        m.gcode(g)
    m.lock_camera(None)
    apply_lights(m, {"chamber": 1.0})
    z = args.z if args.z is not None else m.p["exposure_calibration_z"]
    if m.p["bed_moves_in_z"]:
        px, py = m.park(args.park)
        m.move(px, py, z, m.p["min_clearance"] if args.respect_clearance else z)
    time.sleep(1.5)

    target = args.target if args.target is not None else m.p["exposure_target"]
    chosen = None
    for e in m.p["exposure_ladder"]:
        m.v4l2_set("exposure_time_absolute", e)
        time.sleep(1.5)
        r = m.roi(m.gray())
        mean = r.mean()
        print("  exp %5d  roi mean %6.2f  clipped %.2f%%  dark<8 %.1f%%"
              % (e, mean, 100 * (r >= 254).mean(), 100 * (r < 8).mean()))
        if chosen is None and mean > target:
            chosen = e
    if chosen is None:
        chosen = m.p["exposure_ladder"][-1]
    print("chosen exposure %d (target roi mean %g)" % (chosen, target))
    if args.keep:
        m.v4l2_set("exposure_time_absolute", chosen)
        print("camera left locked at %d" % chosen)
    else:
        m.unlock_camera()
    return chosen


def resolve_z(m, args, default_heights):
    heights = args.heights if args.heights else default_heights
    occupied = getattr(args, "bed", "occupied") == "occupied"
    clearance = args.clearance if args.clearance is not None else (
        m.p["min_clearance"] if occupied else min(heights))
    floor = args.min_z if args.min_z is not None else (clearance if occupied else min(heights))
    heights = [z for z in heights if z >= floor]
    if not heights:
        sys.exit("refusing: min-z %.1f leaves no heights from %s" % (floor, default_heights))
    return heights, clearance, floor


def cmd_sweep(m, args):
    heights, clearance, floor = resolve_z(m, args, m.p["sweep_heights"])
    parks = args.parks if args.parks else list(m.p["parks"])

    print("bed=%s traverse-clearance=%.1f capture-floor=%.1f heights=%s parks=%s"
          % (args.bed, clearance, floor, heights, parks))
    if args.dry_run:
        return

    m.preflight()
    for g in m.p.get("pre_gcode", []):
        m.gcode(g)
    m.lock_camera(args.exposure)
    rec = bedcam.Recorder(args.out, {"profile": m.p["name"], "bed": args.bed,
                                     "clearance": clearance, "exposure": args.exposure,
                                     "gain": m.v4l2_get("gain")})
    states = [{"chamber": 1.0}, {"chamber": 0.0}]
    try:
        m.all_lights_off()
        for park in parks:
            px, py = m.park(park)
            for z in heights:
                m.move(px, py, z, clearance)
                for st in states:
                    apply_lights(m, st)
                    time.sleep(args.settle)
                    rec.add(m, "sweep_%s_z%03.0f_%s.jpg" % (park, z, light_tag(st)),
                            stage="sweep", park=park, x=px, y=py, z=z, lights=st)

        if args.noise:
            px, py = m.park(m.p["default_park"])
            z = max(heights[0], clearance)
            m.move(px, py, z, clearance)
            apply_lights(m, {"chamber": 1.0})
            time.sleep(2.0)
            for i in range(args.noise):
                rec.add(m, "noise_%02d.jpg" % i, stage="noise", park=m.p["default_park"],
                        x=px, y=py, z=z, lights={"chamber": 1.0})
                time.sleep(1.0)
    finally:
        m.unlock_camera()
        m.restore_lights()
        rec.close()


def cmd_probe(m, args):
    heights, clearance, floor = resolve_z(m, args, m.p["probe_heights"])
    park = args.park

    states = [{"chamber": c, "nozzle": 0.0} for c in args.levels]
    states += [{"chamber": 0.0, "nozzle": 1.0}, {"chamber": 1.0, "nozzle": 1.0}]

    print("bed=%s traverse-clearance=%.1f capture-floor=%.1f heights=%s park=%s states=%d"
          % (args.bed, clearance, floor, heights, park, len(states)))
    if args.dry_run:
        return

    m.preflight()
    for g in m.p.get("pre_gcode", []):
        m.gcode(g)
    m.lock_camera(args.exposure)
    rec = bedcam.Recorder(args.out, {"profile": m.p["name"], "stage": "probe",
                                     "clearance": clearance, "exposure": args.exposure,
                                     "gain": m.v4l2_get("gain")})
    try:
        m.all_lights_off()
        px, py = m.park(park)
        for z in heights:
            m.move(px, py, z, clearance)
            for st in states:
                apply_lights(m, st)
                time.sleep(args.settle)
                rec.add(m, "probe_%s_z%03.0f_%s.jpg" % (park, z, light_tag(st)),
                        stage="probe", park=park, x=px, y=py, z=z, lights=st)
    finally:
        m.unlock_camera()
        m.restore_lights()
        rec.close()


def main():
    ap = argparse.ArgumentParser(description="Capture bed-camera calibration data")
    ap.add_argument("--profile", required=True, help="profile name, one of: %s" % ", ".join(bedcam.list_profiles()))
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("exposure", help="ladder the camera exposure and pick one")
    e.add_argument("--z", type=float, default=None)
    e.add_argument("--park", default=None)
    e.add_argument("--target", type=float, default=None)
    e.add_argument("--keep", action="store_true", help="leave the camera locked at the chosen value")
    e.add_argument("--respect-clearance", action="store_true")

    s = sub.add_parser("sweep", help="capture chamber on/off pairs across parks and heights")
    s.add_argument("out")
    s.add_argument("--bed", choices=("empty", "occupied"), required=True)
    s.add_argument("--exposure", type=int, required=True)
    s.add_argument("--heights", type=float, nargs="*", default=None)
    s.add_argument("--parks", nargs="*", default=None)
    s.add_argument("--clearance", type=float, default=None, help="Z floor for XY traverses")
    s.add_argument("--min-z", type=float, default=None, help="lowest capture height")
    s.add_argument("--noise", type=int, default=10)
    s.add_argument("--settle", type=float, default=1.5)
    s.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("probe", help="sweep light levels and sources at a few heights")
    p.add_argument("out")
    p.add_argument("--exposure", type=int, required=True)
    p.add_argument("--heights", type=float, nargs="*", default=None)
    p.add_argument("--park", default=None)
    p.add_argument("--levels", type=float, nargs="*", default=[1.0, 0.66, 0.33, 0.0])
    p.add_argument("--bed", choices=("empty", "occupied"), default="occupied")
    p.add_argument("--clearance", type=float, default=None, help="Z floor for XY traverses")
    p.add_argument("--min-z", type=float, default=None, help="lowest capture height")
    p.add_argument("--settle", type=float, default=1.5)
    p.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()
    m = bedcam.Machine(bedcam.load_profile(args.profile))
    if getattr(args, "park", None) is None:
        args.park = m.p["default_park"]

    {"exposure": cmd_exposure, "sweep": cmd_sweep, "probe": cmd_probe}[args.cmd](m, args)


if __name__ == "__main__":
    main()
