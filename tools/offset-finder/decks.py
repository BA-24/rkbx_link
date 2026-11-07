"""decks.py [seconds] [--decks N] [--version V]: show what rkbx_link would see on each deck.

Reads the chains for the running Rekordbox version from data/offsets and prints
one line per deck: playing or stopped, position, BPM, ANLZ path, track UUID and
whether the UUID's grid file exists. Prints whenever something changes, so leave
it running while you load tracks, press MASTER, stop decks and so on.

Without seconds it prints once, after 0.4 s (it needs a few samples to tell
playing from stopped). See INTERNALS.md for what to look for."""
import argparse
import os
import time

import offsets_file as of
from rbmem import Rekordbox

SHARE = os.path.join(os.environ.get("APPDATA", ""), "Pioneer", "rekordbox", "share")
PLAYING_HOLD = 0.3  # same rule as rkbx_link: moved within the last 300 ms


def uuid_path(uuid):
    if len(uuid) != 36:
        return None
    return os.path.join(SHARE, "PIONEER", "USBANLZ", uuid[:3], uuid[3:], "ANLZ0000.DAT")


def short(path):
    """USBANLZ/fae/5c45c-... -> fae/5c45c"""
    parts = path.replace("\\", "/").split("/")
    return "/".join(parts[-3:-1])[:9] if len(parts) >= 3 else path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seconds", nargs="?", type=float, default=0)
    ap.add_argument("--version")
    ap.add_argument("--decks", type=int, default=2)
    args = ap.parse_args()

    rb = Rekordbox()
    version = args.version or rb.version
    blocks = of.load()
    if version not in blocks:
        raise SystemExit(f"no offsets for {version} in data/offsets")
    block = blocks[version]
    print(f"Rekordbox {rb.version}, using offsets for {version}, {len(block['decks'])} decks")

    last_pos, moved, last = {}, {}, None
    start = time.time()
    end = start + max(args.seconds, 0.4)  # needs a few samples to tell playing from stopped
    while True:
        now = time.time()
        master = rb.value(block["master"], "B")
        rows, key = [], [master]
        for i, d in enumerate(block["decks"][:args.decks]):
            pos = rb.value(d["pos"], "q")
            if pos is not None and last_pos.get(i) not in (None, pos):
                moved[i] = now
            last_pos[i] = pos
            playing = now - moved.get(i, 0) < PLAYING_HOLD
            bpm = rb.value(d["bpm"], "f")
            path = (rb.cstr(d["anlz"]) or b"").decode(errors="replace")
            uuid = (rb.cstr(d["uuid"]) or b"").decode(errors="replace") if d.get("uuid") else ""
            up = uuid_path(uuid)
            via_uuid = "file ok" if up and os.path.exists(up) else ("no file" if up else "-")
            key.append((playing, path, uuid, via_uuid))
            rows.append(
                f"deck {i + 1} {'PLAY' if playing else 'stop'} "
                f"{(pos or 0) / 44100:7.1f}s {bpm or 0:6.2f} bpm  "
                f"path {short(path) if path else 'EMPTY':9}  uuid {uuid[:8] or '-':8} ({via_uuid})"
            )
        if key != last and now - start >= 0.4:
            print(time.strftime("%H:%M:%S"), f"master byte {master} (deck {master + 1 if master is not None else '?'})")
            for r in rows:
                print("   ", r)
            last = key
        if now >= end:
            break
        time.sleep(0.1)


if __name__ == "__main__":
    main()
