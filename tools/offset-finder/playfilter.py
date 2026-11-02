"""Keep f32 hits that sit near an i64 counter advancing at ~audio sample rate."""
import os, struct, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pylib"))
from rbscan import read
from heapscan import find_values

W = 0x400
hits = find_values("f", float(sys.argv[1]))
t0 = time.perf_counter()
snap = {a: read(a - W, 2 * W) for a in hits}
time.sleep(1.0)
dt = time.perf_counter() - t0
for a, b0 in snap.items():
    b1 = read(a - W, 2 * W)
    if not b0 or not b1:
        continue
    for o in range(0, 2 * W, 8):
        v0, v1 = struct.unpack_from("<q", b0, o)[0], struct.unpack_from("<q", b1, o)[0]
        rate = (v1 - v0) / dt
        if 0 < v0 < 48000 * 7200 and 30000 < rate < 60000:
            print(f"hit {a:X}: i64 at {o - W:+X} = {v1}  rate {rate:.0f}/s")
