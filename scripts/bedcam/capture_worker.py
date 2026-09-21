import argparse
import os
import subprocess
import sys
import urllib.request


def v4l2(device, ctrl, value):
    subprocess.run(["v4l2-ctl", "-d", device, "--set-ctrl", "%s=%s" % (ctrl, value)],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("lock", "unlock", "grab"))
    ap.add_argument("--device", default="/dev/video0")
    ap.add_argument("--url", default="http://127.0.0.1:8080/?action=snapshot")
    ap.add_argument("--exposure", type=int, default=625)
    ap.add_argument("--white-balance", type=int, default=4000)
    ap.add_argument("--out")
    args = ap.parse_args()

    if args.action == "lock":
        v4l2(args.device, "auto_exposure", 1)
        v4l2(args.device, "white_balance_automatic", 0)
        v4l2(args.device, "white_balance_temperature", args.white_balance)
        v4l2(args.device, "exposure_time_absolute", args.exposure)
        return 0

    if args.action == "unlock":
        v4l2(args.device, "auto_exposure", 3)
        v4l2(args.device, "white_balance_automatic", 1)
        return 0

    if not args.out:
        sys.exit("grab needs --out")
    with urllib.request.urlopen(args.url, timeout=10) as r:
        data = r.read()
    if not data.startswith(b"\xff\xd8"):
        sys.exit("snapshot did not return a jpeg")
    tmp = args.out + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, args.out)
    print(len(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
