import argparse
import json
import os
import shutil
import sys
import time

import numpy as np

import analyze
import bedcam

CLEAR = 0
NOT_CLEAR = 1
ERROR = 2


def fail(msg):
    sys.stderr.write("bedcheck: %s\n" % msg)
    sys.exit(ERROR)


def marking_score(profile, ref, cur):
    regions = profile.get("marking_regions") or profile.get("exclude_regions", [])
    h, w = ref.shape
    scores = []
    for y0, y1, x0, x1 in regions:
        a = ref[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
        b = cur[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
        if a.size < 400:
            continue
        a = a - a.mean()
        b = b - b.mean()
        n = np.sqrt((a * a).sum() * (b * b).sum())
        if n > 1e-6:
            scores.append(float((a * b).sum() / n))
    if not scores:
        return 0.0
    return min(scores)


def cmd_grab(profile, args):
    m = bedcam.Machine(profile)
    if args.lock:
        m.lock_camera(profile.get("default_exposure", 7500))
        time.sleep(args.settle)
    data = m.snapshot()
    if not data.startswith(b"\xff\xd8"):
        fail("snapshot did not return a jpeg")
    tmp = args.out + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, args.out)
    print(len(data))
    return 0


def cmd_unlock(profile, args):
    bedcam.Machine(profile).unlock_camera()
    return 0


def cmd_lastjob(profile, args):
    import urllib.request
    url = "%s/server/history/list?limit=1" % profile.get("moonraker", "http://127.0.0.1:7125")
    with urllib.request.urlopen(url, timeout=10) as r:
        jobs = json.load(r)["result"]["jobs"]
    if not jobs:
        info = {"found": False}
    else:
        j = jobs[0]
        meta = j.get("metadata") or {}
        info = {"found": True,
                "filename": j.get("filename"),
                "status": j.get("status"),
                "end_time": j.get("end_time"),
                "object_height": meta.get("object_height")}
    print(json.dumps(info))
    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(info, fh)
    return 0


def cmd_verdict(profile, args):
    park = args.park or profile.get("default_park")
    name_on = "sweep_%s_z%03.0f_c100.jpg" % (park, args.z)
    name_off = "sweep_%s_z%03.0f_c000.jpg" % (park, args.z)
    ref_dir = os.path.expanduser(args.reference)
    for n in (name_on, name_off):
        if not os.path.exists(os.path.join(ref_dir, n)):
            fail("reference %s has no %s" % (ref_dir, n))

    lit = bedcam.read_gray(os.path.dirname(args.lit) or ".", os.path.basename(args.lit))
    unlit = bedcam.read_gray(os.path.dirname(args.unlit) or ".", os.path.basename(args.unlit))
    ref_on = bedcam.read_gray(ref_dir, name_on)
    ref_off = bedcam.read_gray(ref_dir, name_off)
    if ref_on.shape != lit.shape:
        fail("reference is %s, camera gave %s" % (ref_on.shape, lit.shape))

    cur = analyze.roi_of(profile, lit - unlit)
    ref = analyze.roi_of(profile, ref_on - ref_off)
    d = np.abs(cur - ref)
    keep = analyze.exclude_mask(profile, d.shape)
    blob, regions = analyze.blob_stats(d, profile.get("blob_threshold", 20), keep)
    mark = marking_score(profile, analyze.roi_of(profile, ref_on), analyze.roi_of(profile, lit))

    if mark < args.marking_threshold:
        verdict = "NO_PLATE"
    elif blob >= args.threshold:
        verdict = "OCCUPIED"
    else:
        verdict = "CLEAR"

    result = {"verdict": verdict, "blob": int(blob), "regions": int(regions),
              "marking": round(mark, 3), "z": args.z, "park": park,
              "threshold": args.threshold, "marking_threshold": args.marking_threshold,
              "reference": ref_dir, "when": time.time()}
    print("bedcheck %s blob=%d regions=%d marking=%.3f" % (verdict, blob, regions, mark))
    if args.save:
        save = os.path.expanduser(args.save)
        os.makedirs(save, exist_ok=True)
        shutil.copyfile(args.lit, os.path.join(save, "lit.jpg"))
        shutil.copyfile(args.unlit, os.path.join(save, "unlit.jpg"))
        with open(os.path.join(save, "result.json"), "w") as fh:
            json.dump(result, fh, indent=1)
        result["saved"] = save
    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(result, fh)
    return CLEAR if verdict == "CLEAR" else NOT_CLEAR


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="trident")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("grab", help="lock the camera and write one snapshot")
    g.add_argument("--out", required=True)
    g.add_argument("--lock", action="store_true")
    g.add_argument("--settle", type=float, default=0.8)

    sub.add_parser("unlock", help="return the camera to auto")

    lj = sub.add_parser("lastjob", help="report the previous job and its object height")
    lj.add_argument("--json-out")

    v = sub.add_parser("verdict", help="compare a lit/unlit pair against a reference")
    v.add_argument("--reference", required=True)
    v.add_argument("--lit", required=True)
    v.add_argument("--unlit", required=True)
    v.add_argument("--z", type=float, default=25.0)
    v.add_argument("--park", default=None)
    v.add_argument("--threshold", type=int, default=200)
    v.add_argument("--marking-threshold", type=float, default=0.5)
    v.add_argument("--json-out")
    v.add_argument("--save")

    args = ap.parse_args()
    profile = bedcam.load_profile(args.profile)
    if args.cmd == "grab":
        return cmd_grab(profile, args)
    if args.cmd == "unlock":
        return cmd_unlock(profile, args)
    if args.cmd == "lastjob":
        return cmd_lastjob(profile, args)
    return cmd_verdict(profile, args)


if __name__ == "__main__":
    sys.exit(main())
