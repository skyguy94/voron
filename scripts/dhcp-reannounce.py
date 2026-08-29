#!/usr/bin/env python3
import os
import random
import socket
import struct
import subprocess
import sys
import time

STAMP = "/run/dhcp-reannounce.stamp"
MIN_INTERVAL = 3600.0
TIMEOUT = 2.0


def default_route():
    fields = subprocess.run(["ip", "-4", "route", "show", "default"],
                            capture_output=True, text=True, check=True).stdout.split()
    return fields[fields.index("via") + 1], fields[fields.index("dev") + 1]


def build_query(name, ident):
    header = struct.pack("!HHHHHH", ident, 0x0100, 1, 0, 0, 0)
    labels = b"".join(struct.pack("!B", len(p)) + p
                      for p in (s.encode("ascii") for s in name.split(".")))
    return header + labels + b"\x00" + struct.pack("!HH", 1, 1)


def answer_count(gateway, name):
    ident = random.randint(0, 0xFFFF)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(TIMEOUT)
    try:
        sock.sendto(build_query(name, ident), (gateway, 53))
        data, _ = sock.recvfrom(2048)
    except (socket.timeout, OSError):
        return None
    finally:
        sock.close()
    if len(data) < 12 or struct.unpack("!H", data[:2])[0] != ident:
        return None
    _, flags, _, ancount, _, _ = struct.unpack("!HHHHHH", data[:12])
    if flags & 0x000F not in (0, 3):
        return None
    return ancount


def connection_for(device):
    out = subprocess.run(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
                         capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        parts = line.rsplit(":", 1)
        if len(parts) == 2 and parts[1] == device:
            return parts[0].replace("\:", ":")
    return None


def throttled():
    try:
        age = time.time() - os.stat(STAMP).st_mtime
    except OSError:
        return False
    return age < MIN_INTERVAL


def fqdn():
    try:
        name = subprocess.run(["hostname", "-f"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        name = ""
    if "." not in name or name.startswith("localhost"):
        name = socket.gethostname().split(".")[0] + ".lan"
    return name


def main():
    name = fqdn()
    gateway, device = default_route()

    count = answer_count(gateway, name)
    if count is None:
        print("%s did not answer for %s, leaving DHCP alone" % (gateway, name))
        return 0
    if count > 0:
        return 0

    if throttled():
        print("%s still missing from %s, but re-announced within the last hour" % (name, gateway))
        return 0

    conn = connection_for(device)
    if conn is None:
        print("no active NetworkManager connection on %s" % device)
        return 1

    print("%s missing from %s, re-running DHCP on %s" % (name, gateway, conn))
    try:
        with open(STAMP, "w") as handle:
            handle.write(str(time.time()))
    except OSError:
        pass
    subprocess.run(["nmcli", "connection", "up", conn], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
