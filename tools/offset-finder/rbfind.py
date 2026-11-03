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
            "title": title_of(track),
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
        if block["decks"][k]["track"] == block["decks"][k]["anlz"]:
            print(f"  [--] {'track info':<20} placeholder (no stable chain known; titles are empty)")
        else:
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


def title_of(raw):
    """'Track Title: X\nArtist: ..' -> 'X', else None."""
    if not raw or not raw.startswith(TITLE):
        return None
    return raw.split(b"\n")[0][len(TITLE):].decode(errors="replace")


def ask_titles(args, decks):
    """Ask the user for (part of) each deck's title; '' means don't check."""
    return [ask(args, f"Type the track title shown on deck {k + 1} (part of it is enough).").lower()
            for k in range(decks)]


def track_matches(rb, chain, di, titles):
    got = [title_of(rb.cstr(scan.deck_chain(chain, di, k), 200)) for k in range(len(titles))]
    if any(g is None for g in got) or len(set(got)) < len(got):
        return False
    return all(t in g.lower() for t, g in zip(titles, got))


def find_track(rb, decks, titles, roots):
    """Track info chains, deck offset at index di: [(chain, di)]."""
    # titles are lower-cased for matching, so search for every title string
    # and keep the ones that contain a typed title
    hits = []
    for a in rb.find_bytes(TITLE):
        t = title_of(rb.read(a, 300).split(b"\0")[0] if rb.read(a, 300) else None)
        if t and (not all(titles) or any(x in t.lower() for x in titles)):
            hits.append(a)
    found = {}
    print(f"  a) from the master/BPM/ANLZ objects ({len(hits)} title strings) ...", flush=True)
    for c in scan.meet_in_middle(rb, roots, hits):
        for di in range(1, len(c) - 1):
            if track_matches(rb, c, di, titles):
                found[tuple(c)] = di
    print(f"     {len(found)} chains")
    print("  b) walking back from the title strings to static memory (a few minutes) ...", flush=True)
    for chain, di in scan.deck_pair_walk(rb, hits, log=lambda m: print(m, flush=True)):
        if track_matches(rb, chain, di, titles):
            found.setdefault(tuple(chain), di)
    good = sorted(([list(c), di] for c, di in found.items()), key=lambda x: (len(x[0]), x[0]))
    return good


def placeholder_track(st, k):
    """No stable track chain: reuse the ANLZ path chain so rkbx_link's reads
    succeed (it drops the connection when any chain fails to read)."""
    return scan.deck_chain(st["anlz"], 1, k)


def search_roots(rb, st):
    """Objects along the BPM, ANLZ and master chains, as forward-search roots."""
    roots, seen = [], set()
    chains = [st["bpm"][:-1], st["anlz"][:-1]] + [c[:-1] for c in st.get("master_cands", [])[:20]]
    for c in chains:
        for n in range(1, len(c) + 1):
            pre = list(c[:n])
            a = rb.resolve(pre + [0])
            obj = rb.u64(a) if a is not None else None
            if obj and (obj, len(pre)) not in seen:
                seen.add((obj, len(pre)))
                roots.append((obj, pre))
    return roots


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
    st.update({"version": version, "decks": args.decks, "process": rb.process_key(), "restarts": 0})

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

    print("\n[4/5] master deck index")
    if args.skip_master and "master_cands" in st:
        print("  (reusing earlier candidates)")
    else:
        st["master_cands"] = master_search(args, rb, st)
        save_state(version, st)
    if not st["master_cands"]:
        sys.exit("  no master chains found")

    print("\n[5/5] track info")
    if args.skip_track and "track_cands" in st:
        print("  (reusing earlier candidates)")
    else:
        titles = ask_titles(args, args.decks)
        st["track_cands"] = find_track(rb, args.decks, titles, search_roots(rb, st))
        save_state(version, st)
    print(f"  {len(st['track_cands'])} candidate chains")
    if not st["track_cands"]:
        print("  none found; rkbx_link will get a placeholder (empty track titles, everything else works)")
    save_state(version, st)
    print("\nNext: quit and restart Rekordbox, load two other tracks, then run:  python rbfind.py confirm")


def toggle_filter(args, rb, cands, rounds, read):
    """Ask for the current master, then toggle `rounds` times; keep chains whose
    byte follows. `cands` is a list of chains, `read(chain)` reads one."""
    ans = ask(args, "Which deck is MASTER right now? (1 or 2)")
    cur = int(ans) - 1 if ans in ("1", "2") else 0
    cands = [c for c in cands if read(c) == cur]
    print(f"  {len(cands)} candidates")
    for _ in range(rounds):
        cur = 1 - cur
        ask(args, f"Click MASTER on deck {cur + 1}.")
        cands = [c for c in cands if read(c) == cur]
        print(f"  {len(cands)} candidates")
    return cands


def master_search(args, rb, st, rounds=7):
    """Find the master byte by toggling MASTER, then every static chain to it."""
    ans = ask(args, "Which deck is MASTER right now? (1 or 2)")
    cur = int(ans) - 1 if ans in ("1", "2") else 0
    print("  checksumming memory pages ...", flush=True)
    before = scan.page_sums(rb)
    cur = 1 - cur
    ask(args, f"Click MASTER on deck {cur + 1}.")
    pages = scan.changed_pages(rb, before)
    cands = scan.bytes_equal(pages, cur)
    print(f"  {len(pages)} pages changed, {scan.bytes_count(cands)} candidate bytes")
    for i in range(rounds):
        if i >= 3 and scan.bytes_count(cands) <= 3:
            break
        cur = 1 - cur
        ask(args, f"Click MASTER on deck {cur + 1}.")
        cands = scan.bytes_filter(rb, cands, cur)
        print(f"  {scan.bytes_count(cands)} candidates")
    addrs = sorted(a + int(o) for a, idx in cands.items() for o in idx)
    if not addrs:
        print("  no candidates left - did each MASTER click register? Retry with:  python rbfind.py new")
        return []
    if len(addrs) > 500:
        print(f"  {len(addrs)} candidate bytes left; keeping the first 500")
        addrs = addrs[:500]
    # walk back to every static chain that reaches these bytes; restarts in
    # `confirm` then remove the ones that go through objects that move
    print(f"  {len(addrs)} address(es); walking back to static memory ...", flush=True)
    chains = scan.back_to_static(rb, addrs, max_depth=4, maxoff=[0x800, 0x400, 0x400, 0x400],
                                 node_cap=3_000_000, per_node=2, log=lambda m: print(m, flush=True))
    chains = toggle_filter(args, rb, chains, 2, lambda c: rb.value(c, "B"))
    return chains[:20000]


def rank_master(chains, st, priors):
    """Best master chain first. A byte inside one deck's own object (reached via
    the BPM/ANLZ deck arrays) is a per-deck 'is master' flag: it follows MASTER
    with 2 decks but not with 4, so those go last. Otherwise prefer the shape
    of known versions' master chains (same final offset, same first offset),
    then shorter chains."""
    deck_arrays = {(st["bpm"][0], st["bpm"][1] + 8 * k) for k in range(4)} | \
                  {(st["anlz"][0], st["anlz"][1] + 8 * k) for k in range(4)}
    prior = [b["master"] for b in priors]

    def score(c):
        per_deck = len(c) > 2 and (c[0], c[1]) in deck_arrays
        same_final = any(c[-1] == p[-1] for p in prior)
        same_first = any(len(c) > 2 and len(p) > 2 and c[1] == p[1] for p in prior)
        return (per_deck, not same_final, not same_first, len(c), c)
    return sorted(chains, key=score)


# -- confirm --------------------------------------------------------------------------

RESTARTS_NEEDED = 2


def cmd_confirm(args):
    rb = Rekordbox()
    version = args.as_version or rb.version
    st = load_state(version)
    if not st:
        sys.exit(f"No saved search for {version}; run `rbfind.py new` first")
    decks = st["decks"]
    if rb.process_key() == st.get("process"):
        sys.exit("  Rekordbox has not been restarted since the last step - quit it, start it again, load tracks, then rerun")
    ask(args, "Load two DIFFERENT tracks than before on deck 1 and 2.")

    for key in ("anlz", "bpm", "pos"):
        print(f"  {key}: {scan.chain_str(st[key])}")
    paths = [_deck_str(rb, st["anlz"], 1, k) for k in range(decks)]
    if not all(anlz_ok(p) for p in paths):
        sys.exit("  ANLZ chain broke after restart (or a deck is empty) - load tracks, or rerun `rbfind.py new`")

    mc = toggle_filter(args, rb, st["master_cands"], 3, lambda c: rb.value(c, "B"))
    print(f"  master deck: {len(mc)} of {len(st['master_cands'])} candidates survived")
    if not mc:
        sys.exit("  no master chain survived - rerun `rbfind.py new`")

    titles = ask_titles(args, decks)
    tc = [(c, di) for c, di in st["track_cands"] if track_matches(rb, c, di, titles)]
    print(f"  track info: {len(tc)} of {len(st['track_cands'])} candidates survived")

    st.update({"master_cands": mc, "track_cands": tc, "process": rb.process_key(),
               "restarts": st.get("restarts", 0) + 1})
    save_state(version, st)
    if st["restarts"] < RESTARTS_NEEDED:
        print(f"\nRestart {st['restarts']} of {RESTARTS_NEEDED} done. Chains that look fine after one restart "
              "can still break on the next, so: quit and restart Rekordbox again, load two other tracks, "
              "and run `rbfind.py confirm` once more.")
        return

    mc = rank_master(mc, st, [b for _, b in prior_blocks(args, of.load(), version)])
    deck_list = []
    for k in range(decks):
        deck_list.append({
            "bpm": scan.deck_chain(st["bpm"], 1, k),
            "pos": scan.deck_chain(st["pos"], 1, k),
            "track": scan.deck_chain(tc[0][0], tc[0][1], k) if tc else placeholder_track(st, k),
            "anlz": scan.deck_chain(st["anlz"], 1, k),
        })
    deck_list = of.stride_extrapolate(deck_list, 4)
    comment = (f"{version} (Windows): found with tools/offset-finder/rbfind.py on {datetime.date.today()}.\n"
               f"Decks 1-{decks} verified across {st['restarts']} restarts; decks {decks + 1}-4 extrapolated (untested).\n"
               f"Other master candidates: {'; '.join(fmt_chain(c) for c in mc[1:4]) or 'none'}")
    if not tc:
        comment += "\nTrack info: no stable chain found; placeholder (ANLZ path chain), titles will be empty."
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
        p.add_argument("--prior", help="only use this known version's shapes")
        if name == "new":
            p.add_argument("--skip-track", action="store_true", help="reuse saved track candidates")
        else:
            p.add_argument("--dry-run", action="store_true", help="print the block, don't write it")
    args = ap.parse_args()
    {"version": cmd_version, "check": cmd_check, "new": cmd_new, "confirm": cmd_confirm}[args.cmd](args)


if __name__ == "__main__":
    main()
