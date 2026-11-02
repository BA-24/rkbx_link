"""Pointer-scan from the playing-deck BPM hits and the paused deck's matching hits."""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rbscan import read
from heapscan import find_values, chains_to, fmt_chain

h0 = [int(x, 16) for x in sys.argv[2:]]
h1 = []
for a in find_values("f", float(sys.argv[1])):
    b = read(a - 0x70, 8)
    if b and 0 < struct.unpack("<q", b)[0] < 48000 * 7200:
        h1.append(a)
print(f"deck2 candidates: {len(h1)}", file=sys.stderr)
c0 = {(g,) + tuple(s) for g, s in chains_to(h0)}
c1 = {(g,) + tuple(s) for g, s in chains_to(h1)}
import json; json.dump({"c0": sorted(c0), "c1": sorted(c1)}, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bpmchains.json"), "w"))
print(f"chains: deck1 {len(c0)}, deck2 {len(c1)}", file=sys.stderr)
for a in sorted(c0):
    for i in range(len(a)):
        for stride in (8, 0x10, 4):
            b = a[:i] + (a[i] + stride,) + a[i + 1:]
            if b in c1:
                print(f"stride {stride:X} @pos {i}: deck1 {fmt_chain(a[0], list(a[1:]))}   deck2 {fmt_chain(b[0], list(b[1:]))}")
