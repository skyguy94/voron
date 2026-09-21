import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import numpy as np
from PIL import Image

PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")
IDLE_STATES = ("standby", "complete", "cancelled", "error")


def load_profile(name):
    path = name if os.path.sep in name else os.path.join(PROFILE_DIR, name + ".json")
    with open(path) as fh:
        return json.load(fh)


def list_profiles():
    return sorted(f[:-5] for f in os.listdir(PROFILE_DIR) if f.endswith(".json"))


class Machine:
    def __init__(self, profile):
        self.p = profile
        self.moonraker = profile.get("moonraker", "http://127.0.0.1:7125")
        self.snapshot_url = profile["snapshot_url"]
        self.device = profile["device"]

    def query(self, objects):
        url = "%s/printer/objects/query?%s" % (self.moonraker, objects)
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.load(r)["result"]["status"]

    def gcode(self, script, timeout=180):
        url = "%s/printer/gcode/script?script=%s" % (self.moonraker, urllib.parse.quote(script))
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)

    def snapshot(self):
        with urllib.request.urlopen(self.snapshot_url, timeout=10) as r:
            return r.read()

    def gray(self, data=None):
        import io
        if data is None:
            data = self.snapshot()
        return np.asarray(Image.open(io.BytesIO(data)).convert("L"), dtype=np.float32)

    def v4l2_set(self, ctrl, value):
        subprocess.run(["v4l2-ctl", "-d", self.device, "--set-ctrl", "%s=%s" % (ctrl, value)],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def v4l2_get(self, ctrl):
        out = subprocess.run(["v4l2-ctl", "-d", self.device, "--get-ctrl", ctrl],
                             capture_output=True, text=True)
        try:
            return int(out.stdout.strip().split(":")[1])
        except (IndexError, ValueError):
            return None

    def lock_camera(self, exposure):
        for ctrl, val in self.p.get("camera_lock", {}).items():
            self.v4l2_set(ctrl, val)
        time.sleep(0.5)
        if exposure:
            self.v4l2_set("exposure_time_absolute", int(exposure))
            time.sleep(0.5)

    def unlock_camera(self):
        for ctrl, val in self.p.get("camera_unlock", {}).items():
            self.v4l2_set(ctrl, val)

    def set_light(self, name, level):
        for tmpl in self.p["lights"][name]:
            self.gcode(tmpl.format(level=level))

    def all_lights_off(self):
        for name in self.p["lights"]:
            self.set_light(name, 0.0)

    def restore_lights(self):
        for name, level in self.p.get("lights_restore", {}).items():
            self.set_light(name, level)

    def roi(self, img):
        t, b, l, r = self.p.get("bed_roi", [0.0, 1.0, 0.0, 1.0])
        h, w = img.shape[:2]
        return img[int(t * h):int(b * h), int(l * w):int(r * w)]

    def park(self, name):
        x, y = self.p["parks"][name]
        return float(x), float(y)

    def preflight(self, require_park=None):
        st = self.query("print_stats&toolhead=homed_axes,position")
        state = st["print_stats"]["state"]
        homed = st["toolhead"]["homed_axes"]
        if state not in IDLE_STATES:
            sys.exit("refusing: print_stats.state is %r, expected one of %s" % (state, IDLE_STATES))
        if "xyz" not in homed:
            sys.exit("refusing: homed_axes is %r, home with a clear bed before running this" % homed)
        pos = st["toolhead"]["position"]
        if require_park is not None:
            px, py = self.park(require_park)
            if abs(pos[0] - px) > 0.5 or abs(pos[1] - py) > 0.5:
                sys.exit("refusing: expected toolhead at %s (X%.1f Y%.1f), found X%.1f Y%.1f"
                         % (require_park, px, py, pos[0], pos[1]))
        return pos

    def move(self, x, y, z, clearance, feed_z=1200, feed_xy=6000):
        self.gcode("G90")
        self.gcode("G1 Z%.2f F%d" % (max(z, clearance), feed_z))
        self.gcode("G1 X%.1f Y%.1f F%d" % (x, y, feed_xy))
        self.gcode("G1 Z%.2f F%d" % (z, feed_z))
        self.gcode("M400")

    def move_z(self, z, feed_z=600):
        self.gcode("G90")
        self.gcode("G1 Z%.2f F%d" % (z, feed_z))
        self.gcode("M400")


class Recorder:
    def __init__(self, outdir, meta):
        self.outdir = outdir
        os.makedirs(outdir, exist_ok=True)
        self.manifest = dict(meta)
        self.manifest["started"] = time.time()
        self.manifest["frames"] = []

    def add(self, machine, name, **fields):
        data = machine.snapshot()
        with open(os.path.join(self.outdir, name), "wb") as fh:
            fh.write(data)
        entry = {"file": name, "bytes": len(data)}
        entry.update(fields)
        self.manifest["frames"].append(entry)
        print(name, len(data))
        return entry

    def close(self):
        self.manifest["finished"] = time.time()
        with open(os.path.join(self.outdir, "manifest.json"), "w") as fh:
            json.dump(self.manifest, fh, indent=1)
        print("wrote %d frames to %s" % (len(self.manifest["frames"]), self.outdir))


def load_manifest(d):
    with open(os.path.join(d, "manifest.json")) as fh:
        return json.load(fh)


def read_gray(d, name):
    return np.asarray(Image.open(os.path.join(d, name)).convert("L"), dtype=np.float32)
