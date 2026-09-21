import argparse
import os

import numpy as np
from PIL import Image

import bedcam


def frames(man, **match):
    out = []
    for f in man["frames"]:
        if all(f.get(k) == v for k, v in match.items()):
            out.append(f)
    return out


def pose_key(f):
    return (f["park"], f["z"])


def roi_of(profile, img):
    t, b, l, r = profile.get("bed_roi", [0.0, 1.0, 0.0, 1.0])
    h, w = img.shape[:2]
    return img[int(t * h):int(b * h), int(l * w):int(r * w)]


def exclude_mask(profile, shape):
    keep = np.ones(shape, bool)
    h, w = shape
    for y0, y1, x0, x1 in profile.get("exclude_regions", []):
        keep[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = False
    return keep


def cmd_noise(args):
    man = bedcam.load_manifest(args.dir)
    fs = frames(man, stage="noise")
    if len(fs) < 2:
        print("no noise frames in %s" % args.dir)
        return
    imgs = [bedcam.read_gray(args.dir, f["file"]) for f in fs]
    d = np.concatenate([np.abs(x - imgs[0]).ravel() for x in imgs[1:]])
    print("noise floor over %d frames" % len(imgs))
    print("  mean abs %.3f  rms %.3f  p99 %.1f  p99.9 %.1f  max %.0f"
          % (d.mean(), np.sqrt((d ** 2).mean()), np.percentile(d, 99),
             np.percentile(d, 99.9), d.max()))
    print("  frac>10 %.4f%%  frac>20 %.4f%%" % (100 * (d > 10).mean(), 100 * (d > 20).mean()))


def cmd_light(args):
    profile = bedcam.load_profile(args.profile)
    man = bedcam.load_manifest(args.dir)
    by_pose = {}
    for f in man["frames"]:
        if f.get("stage") == "noise":
            continue
        by_pose.setdefault(pose_key(f), []).append(f)

    print("light contribution over bed roi (lit minus dark)")
    for key in sorted(by_pose):
        fs = by_pose[key]
        dark = [f for f in fs if all(v == 0.0 for v in f["lights"].values())]
        if not dark:
            continue
        base = roi_of(profile, bedcam.read_gray(args.dir, dark[0]["file"]))
        for f in fs:
            if f is dark[0]:
                continue
            lit = roi_of(profile, bedcam.read_gray(args.dir, f["file"]))
            d = lit - base
            tag = " ".join("%s=%.2f" % (k, v) for k, v in sorted(f["lights"].items()))
            print("  %-4s z%06.2f  %-24s  mean %+7.2f  p90 %6.1f  p99 %6.1f  clip %.2f%%"
                  % (key[0], key[1], tag, d.mean(), np.percentile(d, 90),
                     np.percentile(d, 99), 100 * (lit >= 254).mean()))


def sweep_index(man):
    idx = {}
    for f in man["frames"]:
        if f.get("stage") != "sweep":
            continue
        idx[(f["park"], f["z"], tuple(sorted(f["lights"].items())))] = f
    return idx


def flash_pairs(idx, light="chamber"):
    pairs = {}
    for (park, z, lights), f in idx.items():
        d = dict(lights)
        if list(d) != [light]:
            continue
        pairs.setdefault((park, z), {})[d[light]] = f
    return {k: v for k, v in pairs.items() if 1.0 in v and 0.0 in v}


def blob_stats(d, thresh, keep=None, open_iter=2):
    from scipy import ndimage
    m = d > thresh
    if keep is not None:
        m = m & keep
    m = ndimage.binary_opening(m, np.ones((3, 3)), iterations=open_iter)
    lab, n = ndimage.label(m)
    if n == 0:
        return 0, 0
    return int(ndimage.sum(m, lab, range(1, n + 1)).max()), n


def cmd_compare(args):
    profile = bedcam.load_profile(args.profile)
    a_idx = sweep_index(bedcam.load_manifest(args.empty))
    b_idx = sweep_index(bedcam.load_manifest(args.occupied))

    if args.cancel:
        a_pairs = flash_pairs(a_idx)
        b_pairs = flash_pairs(b_idx)
        shared = sorted(set(a_pairs) & set(b_pairs))
        if not shared:
            print("no poses with both a lit and an unlit frame in each run")
            return
        print("ambient-cancelled detection signal, |(occ_on-occ_off) - (empty_on-empty_off)|")
        for key in shared:
            ea = (roi_of(profile, bedcam.read_gray(args.empty, a_pairs[key][1.0]["file"]))
                  - roi_of(profile, bedcam.read_gray(args.empty, a_pairs[key][0.0]["file"])))
            ob = (roi_of(profile, bedcam.read_gray(args.occupied, b_pairs[key][1.0]["file"]))
                  - roi_of(profile, bedcam.read_gray(args.occupied, b_pairs[key][0.0]["file"])))
            d = np.abs(ob - ea)
            line = ("  %-4s z%06.2f  mean %6.2f  p99 %6.1f  frac>20 %5.2f%%  frac>40 %5.2f%%"
                    % (key[0], key[1], d.mean(), np.percentile(d, 99),
                       100 * (d > 20).mean(), 100 * (d > 40).mean()))
            if args.blob:
                keep = None if args.no_mask else exclude_mask(profile, d.shape)
                biggest, count = blob_stats(d, args.blob_threshold, keep)
                line += "  blob %7d px  (%d regions)" % (biggest, count)
            print(line)
        return

    shared = sorted(set(a_idx) & set(b_idx))
    if not shared:
        print("no matching poses between the two runs")
        return

    print("detection signal, occupied minus empty, over bed roi")
    for key in shared:
        ea = roi_of(profile, bedcam.read_gray(args.empty, a_idx[key]["file"]))
        ob = roi_of(profile, bedcam.read_gray(args.occupied, b_idx[key]["file"]))
        d = np.abs(ob - ea)
        tag = " ".join("%s=%.2f" % (k, v) for k, v in key[2])
        print("  %-4s z%06.2f  %-16s  mean %6.2f  p99 %6.1f  frac>20 %5.2f%%  frac>40 %5.2f%%"
              % (key[0], key[1], tag, d.mean(), np.percentile(d, 99),
                 100 * (d > 20).mean(), 100 * (d > 40).mean()))


def cmd_sheet(args):
    names = sorted(f for f in os.listdir(args.dir)
                   if f.endswith(".jpg") and (args.match is None or args.match in f))
    if not names:
        print("nothing matched")
        return
    names = names[:args.max]
    cols = args.cols
    rows = (len(names) + cols - 1) // cols
    tw, th = args.width, args.width * 3 // 4
    sheet = Image.new("RGB", (cols * tw, rows * th))
    for i, n in enumerate(names):
        sheet.paste(Image.open(os.path.join(args.dir, n)).resize((tw, th)),
                    ((i % cols) * tw, (i // cols) * th))
    sheet.save(args.out, quality=88)
    for i in range(0, len(names), cols):
        print("  row %d: %s" % (i // cols, "  ".join(names[i:i + cols])))
    print("wrote %s (%d tiles)" % (args.out, len(names)))


def main():
    ap = argparse.ArgumentParser(description="Analyze bed-camera calibration data")
    sub = ap.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("noise", help="sensor and pose noise floor from the noise frames")
    n.add_argument("dir")

    l = sub.add_parser("light", help="per-pose contribution of each light source")
    l.add_argument("dir")
    l.add_argument("--profile", required=True)

    c = sub.add_parser("compare", help="occupied versus empty, the actual detection signal")
    c.add_argument("empty")
    c.add_argument("occupied")
    c.add_argument("--profile", required=True)
    c.add_argument("--cancel", action="store_true",
                   help="compare ambient-cancelled lit-minus-unlit images instead of raw frames")
    c.add_argument("--blob", action="store_true",
                   help="also report the largest connected region, the metric that separates "
                        "an object from diffuse plate-reseating error")
    c.add_argument("--blob-threshold", type=float, default=20.0)
    c.add_argument("--no-mask", action="store_true",
                   help="ignore the profile's exclude_regions")

    s = sub.add_parser("sheet", help="contact sheet of frames for eyeballing")
    s.add_argument("dir")
    s.add_argument("--out", default="sheet.jpg")
    s.add_argument("--match", default=None)
    s.add_argument("--cols", type=int, default=3)
    s.add_argument("--width", type=int, default=640)
    s.add_argument("--max", type=int, default=12)

    args = ap.parse_args()
    {"noise": cmd_noise, "light": cmd_light, "compare": cmd_compare, "sheet": cmd_sheet}[args.cmd](args)


if __name__ == "__main__":
    main()
