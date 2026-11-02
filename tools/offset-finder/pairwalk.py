"""Walk back from two per-deck targets; find the level where deck pointers are
adjacent (stride 8), then continue from there to static module memory."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heapscan
from heapscan import back_step, u64_cached, base, size, fmt_chain

heapscan.MAXOFF = int(os.environ.get("MAXOFF", "1000"), 16)
static = lambda a: base <= a < base + size

def step(paths):
    new = {}
    for loc, t in back_step(set(paths)):
        q = u64_cached(loc)
        if q is None:
            continue
        for suf in paths[t][:4]:
            new.setdefault(loc, []).append([t - q] + suf)
    return new

t0, t1 = int(sys.argv[1], 16), int(sys.argv[2], 16)
p0, p1 = {t0: [[]]}, {t1: [[]]}
merged = None
for depth in range(1, 5):
    p0, p1 = step(p0), step(p1)
    pairs = {}
    for loc, sufs in p0.items():
        other = {tuple(s) for s in p1.get(loc + 8, [])}
        for s in sufs:
            if tuple(s) in other:
                pairs.setdefault(loc, []).append(s)
    print(f"depth {depth}: {len(p0)}/{len(p1)} nodes, {len(pairs)} adjacent pairs", flush=True)
    if pairs:
        merged = pairs
        break
if not merged:
    sys.exit("no adjacent deck pointers found")
for loc, sufs in merged.items():
    if static(loc):
        for s in sufs:
            print("STATIC", fmt_chain(loc - base, s))
paths = {k: v for k, v in merged.items() if not static(k)}
for depth in range(4):
    paths = step(paths)
    print(f"  up {depth + 1}: {len(paths)} nodes", flush=True)
    for loc, sufs in paths.items():
        if static(loc):
            for s in sufs:
                print("STATIC", fmt_chain(loc - base, s), flush=True)
    paths = {k: v for k, v in paths.items() if not static(k)}
    if len(paths) > 200_000:
        break
