"""Search strategies for rkbx_link pointer chains in a running rekordbox."""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pylib"))

from rbmem import plausible_ptr

DECK_STRIDE = 8


# -- 1. relaxed search from a known chain shape -------------------------------

def shape_search(rb, prior, test, decks=2, window=0x100, final_window=0x800, log=print):
    """Find chains shaped like `prior` = [G, a, b, F] (deck k uses a + 8k).

    G may be anywhere in rekordbox's writable sections, b may move by `window`
    and F is searched over [0, final_window). `test(p, k)` gets the object
    pointer for deck k and returns the list of final offsets F that hold the
    expected value. Returns chains (deck 1) that validate on every deck.
    """
    _, a, b0, _ = prior
    found, seen = [], set()
    b_lo = max(0, b0 - window)
    for g, v in rb.static_pointers():
        d0 = rb.u64(v + a)
        if not plausible_ptr(d0) or (v, d0) in seen:
            continue
        seen.add((v, d0))
        blk = rb.read(d0 + b_lo, b0 + window - b_lo)
        if not blk:
            continue
        for j in range(0, len(blk), 8):
            p0 = struct.unpack_from("<Q", blk, j)[0]
            if not plausible_ptr(p0):
                continue
            b = b_lo + j
            for f in test(p0, 0):
                ok = True
                for k in range(1, decks):
                    dk = rb.u64(v + a + DECK_STRIDE * k)
                    pk = rb.u64(dk + b) if plausible_ptr(dk) else None
                    if not plausible_ptr(pk) or f not in test(pk, k, f):
                        ok = False
                        break
                if ok:
                    chain = [g, a, b, f]
                    log(f"    found {chain_str(chain)}")
                    found.append(chain)
    return found


def chain_str(c):
    return " ".join(f"{x:X}" for x in c)


# -- 2. backward pointer walk for per-deck strings ------------------------------

def back_step(rb, targets, maxoff):
    """All stored qwords q with 0 <= t - q <= maxoff for some target t -> [(location, t)]."""
    import numpy as np

    T = np.array(sorted(set(targets)), dtype=np.uint64)
    out = []
    for addr, b in rb.blocks():
        vals = np.frombuffer(b[: len(b) // 8 * 8], dtype="<u8")
        lo = np.searchsorted(T, vals, "left")
        hi = np.searchsorted(T, vals + np.uint64(maxoff), "right")
        # one stored pointer can reach several targets; keep all of them
        for j in np.nonzero((hi > lo) & (vals >= 0x10000) & (vals < 0x7FFFFFFFFFFF))[0]:
            q = int(vals[j])
            for i in range(lo[j], hi[j]):
                t = int(T[i])
                out.append((addr + int(j) * 8, t, t - q))
    return out


def deck_pair_walk(rb, targets, max_depth=4, up_depth=4, maxoff=0x1000, node_cap=200_000, log=print):
    """Walk back from a set of strings (one per loaded deck, plus noise) and
    find chains where two decks' pointers sit next to each other (stride 8)
    and end at *different* strings. Then walk on to static memory.

    Returns [(chain for deck 1, index of the deck offset in the chain)]."""
    # paths[loc] = [(suffix offsets, target string)]
    paths = {t: [([], t)] for t in targets}
    merged = None
    for depth in range(1, max_depth + 1):
        new = {}
        for loc, t, off in back_step(rb, paths, maxoff):
            for suf, end in paths.get(t, [])[:4]:
                new.setdefault(loc, []).append(([off] + suf, end))
        paths = new
        pairs = {}
        for loc, entries in paths.items():
            nxt = paths.get(loc + DECK_STRIDE)
            if not nxt:
                continue
            for suf, end in entries:
                if any(s2 == suf and e2 != end for s2, e2 in nxt):
                    pairs.setdefault(loc, []).append((suf, end))
        log(f"    depth {depth}: {len(paths)} nodes, {len(pairs)} deck pairs")
        if pairs:
            merged = pairs
            break
    if not merged:
        return []
    deck_level = depth  # suffix length at the pairing node
    results = []
    paths = merged
    for up in range(up_depth + 1):
        for loc, entries in paths.items():
            if rb.is_static(loc):
                for suf, _ in entries:
                    chain = [loc - rb.base] + suf
                    results.append((chain, len(chain) - 1 - deck_level))
        paths = {k: v for k, v in paths.items() if not rb.is_static(k)}
        if up == up_depth or not paths or len(paths) > node_cap:
            break
        new = {}
        for loc, t, off in back_step(rb, paths, maxoff):
            for suf, end in paths.get(t, [])[:4]:
                new.setdefault(loc, []).append(([off] + suf, end))
        paths = new
        log(f"    up {up + 1}: {len(paths)} nodes, {len(results)} static chains so far")
    # dedupe
    uniq = {}
    for chain, di in results:
        uniq[tuple(chain)] = di
    return [(list(c), di) for c, di in uniq.items()]


def deck_chain(chain, deck_index, k):
    c = list(chain)
    c[deck_index] += DECK_STRIDE * k
    return c


# -- 3. master deck index: byte-level snapshot + toggles ------------------------

MASTER_OBJ = 0x1000


def _master_blocks(rb, roots):
    """Yield (prefix, bytes): prefix () is static data (offsets are module
    offsets), (G,) the object at *G, (G, o) the object at *(*G + o)."""
    for va, data in rb.writable_sections():
        yield ("static", va), data
    for g in sorted(roots):
        v = rb.u64(rb.base + g)
        b1 = rb.read(v, MASTER_OBJ) if plausible_ptr(v) else None
        if not b1:
            continue
        yield (g,), b1
        for o in range(0, MASTER_OBJ, 8):
            p = struct.unpack_from("<Q", b1, o)[0]
            b2 = rb.read(p, MASTER_OBJ) if plausible_ptr(p) else None
            if b2:
                yield (g, o), b2


def _prefix_block(rb, prefix, n):
    if prefix[0] == "static":
        return rb.read(rb.base + prefix[1], n)
    addr = rb.resolve(list(prefix) + [0])
    return rb.read(addr, n) if plausible_ptr(addr) else None


def master_candidates(rb, roots, want):
    """Bytes equal to `want` (current master deck index) in static data and up
    to two pointer levels below each root. Returns {prefix: offsets array}."""
    import numpy as np

    out = {}
    for prefix, data in _master_blocks(rb, roots):
        idx = np.flatnonzero(np.frombuffer(data, dtype=np.uint8) == want).astype(np.uint32)
        if len(idx):
            out[prefix] = idx
    return out


def master_filter(rb, cands, want):
    """Keep candidates whose byte now equals `want`; re-reads each block once."""
    import numpy as np

    out = {}
    for prefix, idx in cands.items():
        if prefix == ("static", 0):  # sparse, from saved JSON: read one by one
            keep = np.array([i for i in idx if rb.value([int(i)], "B") == want], dtype=np.uint32)
            if len(keep):
                out[prefix] = keep
            continue
        b = _prefix_block(rb, prefix, int(idx.max()) + 1)
        if not b:
            continue
        keep = idx[np.frombuffer(b, dtype=np.uint8)[idx] == want]
        if len(keep):
            out[prefix] = keep
    return out


def master_count(cands):
    return sum(len(v) for v in cands.values())


def master_chains(cands):
    """Flatten to rkbx_link chains, simplest first."""
    chains = []
    for prefix, idx in cands.items():
        for i in idx:
            if prefix[0] == "static":
                chains.append([prefix[1] + int(i)])
            else:
                chains.append(list(prefix) + [int(i)])
    return sorted(chains, key=lambda c: (len(c), c[-1] % 4 != 0, c))


def master_from_chains(chains):
    """Inverse of master_chains, for candidates saved as JSON."""
    import numpy as np

    out = {}
    for c in chains:
        out.setdefault(tuple(c[:-1]), []).append(c[-1])
    # single-element chains are static: rebase to a ("static", 0) block
    if () in out:
        out[("static", 0)] = out.pop(())
    return {k: np.array(sorted(v), dtype=np.uint32) for k, v in out.items()}
