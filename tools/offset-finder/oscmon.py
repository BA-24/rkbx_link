"""oscmon.py [seconds] [port]: listen for rkbx_link OSC output and summarise it.

Prints each /master/beat/trigger as it arrives, then the last value per address
and the measured beat interval."""
import socket
import struct
import sys
import time


def pad(n):
    return (n + 4) & ~3


def decode(d):
    addr = d.split(b"\0", 1)[0]
    i = pad(len(addr))
    tags = d[i:].split(b"\0", 1)[0]
    j = i + pad(len(tags))
    if tags[1:2] == b"f":
        val = struct.unpack(">f", d[j:j + 4])[0]
    elif tags[1:2] == b"i":
        val = struct.unpack(">i", d[j:j + 4])[0]
    else:
        val = d[j:].split(b"\0", 1)[0].decode(errors="replace")
    return addr.decode(), val


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 20
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 4460
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", port))
    s.settimeout(0.5)
    end, last, beats = time.time() + secs, {}, []
    while time.time() < end:
        try:
            d, _ = s.recvfrom(4096)
        except socket.timeout:
            continue
        addr, val = decode(d)
        last[addr] = val
        if addr.startswith("/master/beat/trigger/") and val:
            beats.append(time.time())
            print(f"{beats[-1]:.3f} {addr}", flush=True)
    for k in sorted(last):
        print(f"{k:32} {last[k]}")
    if len(beats) > 2:
        d = [b - a for a, b in zip(beats, beats[1:])][1:]
        m = sum(d) / len(d)
        print(f"{len(beats)} beats, mean {m * 1000:.1f} ms = {60 / m:.2f} BPM, "
              f"max jitter {max(abs(x - m) for x in d) * 1000:.1f} ms")


if __name__ == "__main__":
    main()
