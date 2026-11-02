"""rbfind: find and check rkbx_link offsets for the running Rekordbox.

Run with Windows Python while Rekordbox is open in Performance mode:

  python rbfind.py version          detected Rekordbox version, and whether it's supported
  python rbfind.py check [VERSION]  live-check a version's offsets (default: the running one)
  python rbfind.py new              guided search for an unsupported version
  python rbfind.py confirm          after restarting Rekordbox: drop chains that broke,
                                    then write the new block to data/offsets

`new` and `confirm` keep their progress in work/<version>/state.json.
"""
import argparse
import datetime
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anlz  # noqa: E402
import offsets_file as of  # noqa: E402
import scan  # noqa: E402
from rbmem import Rekordbox, fmt_chain  # noqa: E402

SAMPLE_RATE = 44100
TITLE = b"Track Title: "


# -- helpers ---------------------------------------------------------------------

def ask(args, msg):
    if args.yes:
        print(f"[--yes] {msg}")
        return ""
    return input(f"\n>>> {msg}\n    Press Enter when done (or type an answer): ").strip()


def state_path(version):
    d = os.path.join(HERE, "work", version)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "state.json")


def load_state(version):
    p = state_path(version)
    return json.load(open(p)) if os.path.exists(p) else {}


def save_state(version, st):
    json.dump(st, open(state_path(version), "w"), indent=1)


def anlz_ok(s):
    return bool(s) and s.endswith(b".DAT") and b"ANLZ" in s


def read_decks(rb, block, decks):
    """Snapshot every field of every deck using a version block."""
    out = []
    for k in range(decks):
        d = block["decks"][k]
        path = rb.cstr(d["anlz"])
        track = rb.cstr(d["track"], 200)
        out.append({
            "bpm": rb.value(d["bpm"], "f"),
            "pos": rb.value(d["pos"], "q"),
            "anlz": path.decode(errors="replace") if path else None,
            "title": track.split(b"\n")[0][len(TITLE):].decode(errors="replace")
            if track and track.startswith(TITLE) else None,
        })
    return out


# -- check -------------------------------------------------------------------------

def cmd_version(args):
    rb = Rekordbox()
    blocks = of.load()
    print(f"Rekordbox {rb.version}  ({rb.exe_path})")
    if rb.version in blocks:
        print(f"Supported: data/offsets has a block for {rb.version}. Set keeper.rekordbox_version {rb.version}")
    else:
        print(f"NOT supported yet. Known versions: {', '.join(blocks)}")
        print("Run:  python rbfind.py new")


def cmd_check(args):
    rb = Rekordbox()
    version = args.version or rb.version
    blocks = of.load()
    if version not in blocks:
        sys.exit(f"No offsets for {version} in data/offsets (known: {', '.join(blocks)})")
    if version != rb.version:
        print(f"warning: checking {version} offsets against running Rekordbox {rb.version}")
    ok = check_block(rb, blocks[version], args.decks)
    sys.exit(0 if ok else 1)


def check_block(rb, block, decks):
    master = rb.value(block["master"], "B")
    a = read_decks(rb, block, decks)
    time.sleep(0.5)
    b = read_decks(rb, block, decks)
    good = True

    def report(label, passed, detail):
        nonlocal good
        good &= passed
        print(f"  [{'ok' if passed else 'FAIL'}] {label:<20} {detail}")

    print(f"Rekordbox {rb.version}, {decks} decks")
    report("master deck", master is not None and master < decks, f"deck {master + 1 if master is not None else '?'}")
    for k in range(decks):
        x, y = a[k], b[k]
        print(f" deck {k + 1}:")
        loaded = anlz_ok((x["anlz"] or "").encode())
        report("ANLZ path", loaded, x["anlz"] or "(none - load a track)")
        if not loaded:
            good = False
            continue
        grid = anlz.beat_grid(x["anlz"])
        length = grid[-1][0] / 1000 if grid else None
        pos_ok = x["pos"] is not None and 0 <= x["pos"] <= ((length or 1e9) + 30) * SAMPLE_RATE
        report("sample position", pos_ok, f"{x['pos']} ({x['pos'] / SAMPLE_RATE:.1f}s)" if x["pos"] is not None else "unreadable")
        if pos_ok and y["pos"] is not None:
            rate = (y["pos"] - x["pos"]) / 0.5
            if rate:
                report("playback rate", 0.5 * SAMPLE_RATE < abs(rate) < 1.6 * SAMPLE_RATE, f"{rate:.0f} samples/s")
        grid_bpm = anlz.bpm_at(grid, x["pos"] / SAMPLE_RATE) if pos_ok else None
        bpm = x["bpm"]
        bpm_ok = bpm is not None and 20 < bpm < 400
        note = f"{bpm:.2f}" if bpm else "unreadable"
        if bpm_ok and grid_bpm:
            note += f" (grid {grid_bpm:.2f}, pitch {100 * (bpm / grid_bpm - 1):+.2f}%)"
            bpm_ok = abs(bpm / grid_bpm - 1) < 0.5
        report("BPM", bpm_ok, note)
        report("track info", x["title"] is not None, repr(x["title"]))
    if decks >= 2 and all(d["anlz"] for d in a) and len({d["anlz"] for d in a}) < decks:
        print("  note: decks share the same ANLZ path - load different tracks for a meaningful check")
    print("PASS" if good else "FAIL")
    return good


# -- new -----------------------------------------------------------------------------

def prior_blocks(args, blocks, version):
    names = [args.prior] if args.prior else [v for v in of.nearest(blocks, version) if v != version]
    return [(v, blocks[v]) for v in names]


def find_anlz(rb, priors, decks):
    shapes = {tuple([0] + c[1:]) for _, b in priors for c in [b["decks"][0]["anlz"]] if len(c) == 4}

    def test(p, k, f=None):
        return [0] if anlz_ok(_str_at(rb, p)) else []

    for shape in sorted(shapes):
        print(f"  trying ANLZ shape {scan.chain_str(list(shape)[1:])} ...")
        res = scan.shape_search(rb, list(shape), test, decks, final_window=8)
        res = [c for c in res if len({_deck_str(rb, c, 1, k) for k in range(decks)}) == decks]
        if res:
            return res
    return []


def _str_at(rb, addr, n=500):
    while n >= 16:
        b = rb.read(addr, n)
        if b is not None:
            return b.split(b"\0", 1)[0]
        n //= 2
    return None


def _deck_str(rb, chain, idx, k):
    return rb.cstr(scan.deck_chain(chain, idx, k))


def find_bpm(rb, priors, decks, expected):
    shapes = {tuple([0] + c[1:]) for _, b in priors for c in [b["decks"][0]["bpm"]] if len(c) == 4}
    pats = [[x for t in expected[k] for x in f32_near(t)] for k in range(decks)]

    def test(p, k, f=None):
        if f is not None:
            b = rb.read(p + f, 4)
            return [f] if b in pats[k] else []
        blk = rb.read(p, 0x800)
        if not blk:
            return []
        hits = []
        for pat in pats[k]:
            i = blk.find(pat)
            while i != -1:
                if i % 4 == 0:
                    hits.append(i)
                i = blk.find(pat, i + 1)
        return hits

    for shape in sorted(shapes):
        print(f"  trying BPM shape {scan.chain_str(list(shape)[1:])} ...")
        res = scan.shape_search(rb, list(shape), test, decks)
        if res:
            return res
    return []


def f32_near(x):
    """x as f32 bytes, plus one ulp either side (grid tempos like 127.99 round differently)."""
    i = struct.unpack("<I", struct.pack("<f", x))[0]
    return [struct.pack("<I", j) for j in (i - 1, i, i + 1)]


def find_pos(rb, priors, decks, bpm_chain, lengths):
    """Sample position lives in the BPM object; try known BPM->position deltas,
    then any i64 in the object that looks like a playback counter."""
    deltas = []
    for _, b in priors:
        d = b["decks"][0]
        if d["pos"][:-1][1:] == d["bpm"][:-1][1:]:
            deltas.append(d["pos"][-1] - d["bpm"][-1])
    base = bpm_chain[:-1]
    known = [bpm_chain[-1] + dl for dl in dict.fromkeys(deltas)]
    cands = list(known)
    cands += [f for f in range(max(0, bpm_chain[-1] - 0x400), bpm_chain[-1] + 0x400, 8) if f not in cands]

    def sample(f):
        return [rb.value(scan.deck_chain(base + [f], 1, k), "q") for k in range(decks)]

    first = {f: sample(f) for f in cands}
    time.sleep(1.0)
    best = []
    for f in cands:
        a, b = first[f], sample(f)
        if any(v is None for v in a + b):
            continue
        in_range = all(0 <= v <= (lengths[k] + 30) * SAMPLE_RATE for k, v in enumerate(b))
        rates = [(y - x) for x, y in zip(a, b)]
        playing = [0.7 * SAMPLE_RATE < r < 1.5 * SAMPLE_RATE for r in rates]
        still = [r == 0 for r in rates]
        if in_range and all(p or s for p, s in zip(playing, still)):
            best.append((f in known, any(playing), f))
    best.sort(reverse=True)
    return [(base + [f], playing) for _, playing, f in best]


def find_track(rb, decks):
    hits = [a for a in rb.find_bytes(TITLE)]
    print(f"  {len(hits)} 'Track Title:' strings in memory; walking back to static memory (takes a few minutes) ...")
    res = scan.deck_pair_walk(rb, hits, log=lambda m: print(m, flush=True))
    good = []
    for chain, di in res:
        titles = [rb.cstr(scan.deck_chain(chain, di, k), 200) for k in range(decks)]
        if all(t and t.startswith(TITLE) for t in titles) and len(set(titles)) == decks:
            good.append((chain, di))
    good.sort(key=lambda x: (len(x[0]), x[0]))
    return good


def cmd_new(args):
    rb = Rekordbox()
    version = args.as_version or rb.version
    blocks = of.load()
    if version in blocks and not args.as_version:
        print(f"{version} is already supported. Use `rbfind.py check`.")
        return
    priors = prior_blocks(args, blocks, version)
    print(f"Rekordbox {rb.version}: searching offsets using shapes from {', '.join(v for v, _ in priors)}")
    st = load_state(version)
    st.update({"version": version, "decks": args.decks})

    ask(args, "In Rekordbox (Performance mode): load DIFFERENT tracks on deck 1 and deck 2, "
              "keep pitch/tempo at 0%, start deck 1 playing and leave deck 2 paused.")

    print("\n[1/5] ANLZ path")
    res = find_anlz(rb, priors, args.decks)
    if not res:
        sys.exit("  could not find the ANLZ path chain - see README (manual steps)")
    st["anlz"] = res[0]
    paths = [_deck_str(rb, res[0], 1, k).decode() for k in range(args.decks)]
    grids = [anlz.beat_grid(p) for p in paths]
    for k, p in enumerate(paths):
        print(f"  deck {k + 1}: {p}  tempos {anlz.tempos(grids[k])[:5]}")

    print("\n[2/5] BPM")
    res = find_bpm(rb, priors, args.decks, [anlz.tempos(g) for g in grids])
    if not res:
        sys.exit("  could not find the BPM chain - make sure pitch is 0% on both decks")
    st["bpm"] = res[0]
    if len(res) > 1:
        print(f"  {len(res)} BPM chains, using the first")

    print("\n[3/5] sample position")
    lengths = [g[-1][0] / 1000 if g else 3600 for g in grids]
    res = find_pos(rb, priors, args.decks, st["bpm"], lengths)
    if res and not res[0][1]:
        print("  warning: no deck seems to be playing; position is unverified")
    if not res:
        sys.exit("  could not find the sample position - start deck 1 playing and retry")
    st["pos"] = res[0][0]
    print(f"    using {scan.chain_str(st['pos'])}")
    save_state(version, st)

    print("\n[4/5] track info")
    if args.skip_track and "track_cands" in st:
        print("  (reusing earlier candidates)")
    else:
        st["track_cands"] = find_track(rb, args.decks)
        save_state(version, st)
    print(f"  {len(st['track_cands'])} candidate chains (a Rekordbox restart in `confirm` removes the false ones)")
    if not st["track_cands"]:
        sys.exit("  no track info chains found - see README (manual steps)")

    print("\n[5/5] master deck index")
    if args.skip_master:
        print("  skipped (--skip-master)")
    else:
        st["master_cands"] = master_search(args, rb, st)
    save_state(version, st)
    print("\nNext: quit and restart Rekordbox, load two different tracks, then run:  python rbfind.py confirm")


def master_search(args, rb, st, cands=None, rounds=4):
    """Toggle MASTER between decks until few candidates remain; returns chains."""
    roots = {st["bpm"][0], st["anlz"][0]} | {c[0][0] for c in st["track_cands"]}
    ans = ask(args, "Which deck is MASTER right now? (1 or 2)")
    cur = int(ans) - 1 if ans in ("1", "2") else 0
    if cands is None:
        print("  snapshotting memory ...")
        cands = scan.master_candidates(rb, roots, cur)
    else:
        cands = scan.master_filter(rb, scan.master_from_chains(cands), cur)
    print(f"  {scan.master_count(cands)} candidates")
    for _ in range(rounds):
        if scan.master_count(cands) <= 3:
            break
        cur = 1 - cur
        ask(args, f"Click MASTER on deck {cur + 1}.")
        cands = scan.master_filter(rb, cands, cur)
        print(f"  {scan.master_count(cands)} candidates")
    if not scan.master_count(cands):
        print("  no candidates left - did each MASTER click register? Retry with:  python rbfind.py new --skip-track")
    return scan.master_chains(cands)[:1000]


# -- confirm --------------------------------------------------------------------------

def cmd_confirm(args):
    rb = Rekordbox()
    version = args.as_version or rb.version
    st = load_state(version)
    if not st:
        sys.exit(f"No saved search for {version}; run `rbfind.py new` first")
    decks = st["decks"]
    ask(args, "After restarting Rekordbox: load two DIFFERENT tracks than before on deck 1 and 2.")

    for key in ("anlz", "bpm", "pos"):
        print(f"  {key}: {scan.chain_str(st[key])}")
    paths = [_deck_str(rb, st["anlz"], 1, k) for k in range(decks)]
    if not all(anlz_ok(p) for p in paths):
        sys.exit("  ANLZ chain broke after restart - rerun `rbfind.py new`")

    tc = [(c, di) for c, di in st["track_cands"]
          if all((t := rb.cstr(scan.deck_chain(c, di, k), 200)) and t.startswith(TITLE) for k in range(decks))
          and len({rb.cstr(scan.deck_chain(c, di, k), 200) for k in range(decks)}) == decks]
    print(f"  track info: {len(tc)} of {len(st['track_cands'])} candidates survived the restart")
    if not tc:
        sys.exit("  no track chain survived - rerun `rbfind.py new`")

    if args.skip_master or not st.get("master_cands"):
        sys.exit("  master deck candidates missing - run `rbfind.py new` without --skip-master")
    mc = master_search(args, rb, st, cands=st["master_cands"], rounds=2)
    print(f"  master deck: {len(mc)} candidates survived")
    if not mc:
        sys.exit("  no master chain survived - rerun `rbfind.py new`")

    track, di = tc[0]
    deck_list = []
    for k in range(decks):
        deck_list.append({
            "bpm": scan.deck_chain(st["bpm"], 1, k),
            "pos": scan.deck_chain(st["pos"], 1, k),
            "track": scan.deck_chain(track, di, k),
            "anlz": scan.deck_chain(st["anlz"], 1, k),
        })
    deck_list = of.stride_extrapolate(deck_list, 4)
    comment = (f"{version} (Windows): found with tools/offset-finder/rbfind.py on {datetime.date.today()}.\n"
               f"Decks 1-{decks} verified across a restart; decks {decks + 1}-4 extrapolated (untested).\n"
               f"Other master candidates: {'; '.join(fmt_chain(c) for c in mc[1:4]) or 'none'}")
    block = of.format_block(version, mc[0], deck_list, comment)
    print("\n" + block)
    blocks = of.load()
    if args.dry_run:
        print("(--dry-run: not writing)")
    elif version in blocks:
        print(f"{version} already in data/offsets - not writing. Compare with the block above.")
    elif args.yes or input("Append this block to data/offsets? [y/N] ").lower().startswith("y"):
        of.append_block(block)
        print("Written. Checking ...")
        check_block(rb, of.load()[version], decks)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("version")
    c = sub.add_parser("check")
    c.add_argument("version", nargs="?")
    c.add_argument("--decks", type=int, default=2)
    for name in ("new", "confirm"):
        p = sub.add_parser(name)
        p.add_argument("--decks", type=int, default=2)
        p.add_argument("--yes", action="store_true", help="don't wait for prompts (testing)")
        p.add_argument("--skip-master", action="store_true")
        p.add_argument("--as-version", help="treat the running Rekordbox as this version (testing)")
        if name == "new":
            p.add_argument("--prior", help="only use this known version's shapes")
            p.add_argument("--skip-track", action="store_true", help="reuse saved track candidates")
        else:
            p.add_argument("--dry-run", action="store_true", help="print the block, don't write it")
    args = ap.parse_args()
    {"version": cmd_version, "check": cmd_check, "new": cmd_new, "confirm": cmd_confirm}[args.cmd](args)


if __name__ == "__main__":
    main()
