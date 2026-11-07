"""Read/write rkbx_link's data/offsets format (mirrors src/offsets.rs).

A version block is: version line, master deck index pointer, then per deck
4 pointers (BPM, sample position, track info, ANLZ path), optionally followed
by a "#uuid <chain>" line (this fork only: the loaded track's UUID). Blocks are
ended by two blank lines; other lines starting with '#' are ignored."""
import os
import re

from rbmem import fmt_chain, parse_chain

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OFFSETS = os.path.join(REPO, "data", "offsets")
FIELDS = ("bpm", "pos", "track", "anlz")


def load(path=OFFSETS):
    """Return {version: {"master": chain, "decks": [{field: chain}]}} in file order."""
    blocks, lines, empty = {}, [], 0
    for line in open(path).read().replace("\r", "").split("\n") + ["", ""]:
        if not line:
            empty += 1
            if empty >= 2 and lines:
                v, master = lines[0], parse_chain(lines[1])
                decks, cur = [], []
                for x in lines[2:]:
                    if x.startswith("uuid "):
                        decks[-1]["uuid"] = parse_chain(x[5:])
                        continue
                    cur.append(parse_chain(x))
                    if len(cur) == 4:
                        decks.append(dict(zip(FIELDS, cur)))
                        cur = []
                blocks[v] = {"master": master, "decks": decks}
                lines = []
        else:
            empty = 0
            if line.startswith("#uuid "):
                lines.append(line[1:].strip())
            elif not line.startswith("#"):
                lines.append(line.strip())
    return blocks


def version_key(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


def nearest(blocks, version):
    """Known versions ordered by closeness to `version` (newest first among ties)."""
    vk = version_key(version)
    return sorted(blocks, key=lambda v: (abs(_num(version_key(v)) - _num(vk)), -_num(version_key(v))))


def _num(k):
    k = (list(k) + [0, 0, 0])[:3]
    return k[0] * 1_000_000 + k[1] * 1_000 + k[2]


def stride_extrapolate(decks, n=4):
    """Fill decks up to n by repeating the deck-1 -> deck-2 difference."""
    decks = [dict(d) for d in decks]
    while len(decks) < n and len(decks) >= 2:
        a, b = decks[-2], decks[-1]
        nxt = {}
        for f in FIELDS:
            nxt[f] = [y + (y - x) for x, y in zip(a[f], b[f])] if len(a[f]) == len(b[f]) else list(b[f])
        decks.append(nxt)
    return decks


def format_block(version, master, decks, comment=None):
    out = []
    if comment:
        out += [f"# {line}" for line in comment.split("\n")]
    out += [version, fmt_chain(master), ""]
    for d in decks:
        out += [fmt_chain(d[f]) for f in FIELDS]
        out += [f"#uuid {fmt_chain(d['uuid'])}"] if d.get("uuid") else []
        out += [""]
    return "\n".join(out) + "\n\n"


def append_block(text, path=OFFSETS):
    with open(path, "a", newline="\n") as f:
        f.write("\n" + text)
