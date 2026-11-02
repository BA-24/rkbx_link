"""master2.py init|filter <expected u8>: byte-level master-deck flag search,
rooted at globals confirmed to survive a restart (depth <= 2 below each)."""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rbscan import base, read, u64, sections, plausible_ptr

HERE = os.path.dirname(os.path.abspath(__file__))
CANDS = os.path.join(HERE, "master2_cands.txt")
OBJ = 0x1000


def roots():
    gs = {0x5E09790, 0x5F53DA8}
    for line in open(os.path.join(HERE, "track_pass2.txt")):
        gs.add(int(line.split()[1], 16))
    return gs


def blocks():
    """Yield (chain prefix, block address, bytes); chain = prefix + [byte offset]."""
    for name, va, vsize, chars in sections():
        if chars & 0x80000000:
            b = read(base + va, vsize)
            if b:
                yield [], base + va, b, va
    for g in sorted(roots()):
        v = u64(base + g)
        if not v or not plausible_ptr(v):
            continue
        b1 = read(v, OBJ)
        if not b1:
            continue
        yield [g], v, b1, 0
        for o in range(0, OBJ, 8):
            p = struct.unpack_from("<Q", b1, o)[0]
            if plausible_ptr(p):
                b2 = read(p, OBJ)
                if b2:
                    yield [g, o], p, b2, 0


if sys.argv[1] == "init":
    want = int(sys.argv[2])
    out = []
    for prefix, addr, b, rel in blocks():
        for i, x in enumerate(b):
            if x == want:
                out.append(" ".join(f"{v:X}" for v in prefix + [rel + i]) + "\n")
    open(CANDS, "w").writelines(out)
    print(f"{len(out)} candidates")
else:
    sys.path.insert(0, HERE)
    from verify import val
    want = int(sys.argv[2])
    keep = [l for l in open(CANDS) if val([int(x, 16) for x in l.split()], "B") == want]
    open(CANDS, "w").writelines(keep)
    print(f"{len(keep)} kept")
    if len(keep) < 60:
        print("".join(keep))
