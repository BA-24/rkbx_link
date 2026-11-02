"""Backward pointer scan in rekordbox.exe: find static chains to per-deck values.

usage: heapscan.py f32 175.0 160.0     # deck 1 value, deck 2 value
Chains are printed in rkbx_link offsets syntax: "G o1 .. on F".
"""
import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pylib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
from rbscan import base, size, h, k32, read  # noqa: E402

MAXOFF = 0x1000
MAXDEPTH = 5
NODE_CAP = 300_000


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_ulonglong), ("AllocationBase", ctypes.c_ulonglong),
                ("AllocationProtect", wt.DWORD), ("PartitionId", wt.WORD),
                ("RegionSize", ctypes.c_ulonglong), ("State", wt.DWORD),
                ("Protect", wt.DWORD), ("Type", wt.DWORD)]


k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t


def regions():
    addr, mbi = 0, MBI()
    while k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        rw = mbi.Protect & 0xFF in (0x04, 0x08, 0x40) and not mbi.Protect & 0x100
        in_module = base <= mbi.BaseAddress < base + size
        if mbi.State == 0x1000 and rw and (mbi.Type == 0x20000 or in_module):
            yield mbi.BaseAddress, mbi.RegionSize
        addr = mbi.BaseAddress + mbi.RegionSize
        if addr >= 0x7FFFFFFFFFFF:
            break


def blocks(chunk=64 << 20):
    for start, length in regions():
        for off in range(0, length, chunk):
            n = min(chunk, length - off)
            b = read(start + off, n)
            if b:
                yield start + off, b


def find_values(fmt, value):
    pat = struct.pack("<" + fmt, value)
    align = len(pat) if fmt == "d" else 4
    hits = []
    for addr, b in blocks():
        i = b.find(pat)
        while i != -1:
            if i % align == 0:
                hits.append(addr + i)
            i = b.find(pat, i + 1)
    return hits


def back_step(targets):
    """For each stored qword q with 0 <= t - q <= MAXOFF, yield (location, t)."""
    T = np.array(sorted(set(targets)), dtype=np.uint64)
    out = []
    for addr, b in blocks():
        vals = np.frombuffer(b[: len(b) // 8 * 8], dtype="<u8")
        idx = np.searchsorted(T, vals)
        ok = idx < len(T)
        idx_c = np.where(ok, idx, 0)
        diff = T[idx_c] - vals
        m = ok & (diff <= MAXOFF) & (vals >= 0x10000)
        for j in np.nonzero(m)[0]:
            out.append((addr + int(j) * 8, int(T[idx_c[j]])))
    return out


def chains_to(hits):
    """Return chains (G, [offsets...], F) from static module memory to any hit."""
    # paths[node] = list of offset-suffixes from node to a hit
    paths = {t: [[]] for t in hits}
    found = []
    frontier = set(hits)
    for depth in range(MAXDEPTH):
        steps = back_step(frontier)
        new_paths = {}
        for loc, t in steps:
            q = u64_cached(loc)
            if q is None:
                continue
            off = t - q
            for suf in paths.get(t, [])[:8]:
                new_paths.setdefault(loc, []).append([off] + suf)
        print(f"  depth {depth + 1}: {len(new_paths)} nodes", file=sys.stderr, flush=True)
        for loc, sufs in new_paths.items():
            if base <= loc < base + size:
                for s in sufs:
                    found.append((loc - base, s))
        nonstatic = {k: v for k, v in new_paths.items() if not base <= k < base + size}
        if len(nonstatic) > NODE_CAP:
            print("  node cap hit, stopping", file=sys.stderr)
            break
        paths, frontier = nonstatic, set(nonstatic)
    return found


def u64_cached(loc):
    b = read(loc, 8)
    return None if b is None else struct.unpack("<Q", b)[0]


def fmt_chain(g, suf):
    return " ".join(f"{x:X}" for x in [g] + suf)


def main():
    fmt, v0, v1 = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    h0, h1 = find_values(fmt, v0), find_values(fmt, v1)
    print(f"{v0}: {len(h0)} hits, {v1}: {len(h1)} hits", file=sys.stderr)
    c0 = {(g,) + tuple(s) for g, s in chains_to(h0)}
    c1 = {(g,) + tuple(s) for g, s in chains_to(h1)}
    print(f"chains: deck1 {len(c0)}, deck2 {len(c1)}", file=sys.stderr)
    # A per-deck chain pair differs in exactly one position, by a small stride.
    for a in sorted(c0):
        for i in range(len(a)):
            for stride in (8, 0x10, 4):
                b = a[:i] + (a[i] + stride,) + a[i + 1:]
                if b in c1:
                    print(f"stride {stride:X} @pos {i}: deck1 {fmt_chain(a[0], list(a[1:]))}"
                          f"   deck2 {fmt_chain(b[0], list(b[1:]))}")


if __name__ == "__main__":
    main()
