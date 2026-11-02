"""Read/write rkbx_link's data/offsets format (mirrors src/offsets.rs).

A version block is: version line, master deck index pointer, then per deck
4 pointers (BPM, sample position, track info, ANLZ path). Blocks are ended by
two blank lines; lines starting with '#' are ignored."""
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
                v, master, rest = lines[0], parse_chain(lines[1]), [parse_chain(x) for x in lines[2:]]
                decks = [dict(zip(FIELDS, rest[i:i + 4])) for i in range(0, len(rest) - 3, 4)]
                blocks[v] = {"master": master, "decks": decks}
                lines = []
        else:
            empty = 0
            if not line.startswith("#"):
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
        out += [fmt_chain(d[f]) for f in FIELDS] + [""]
    return "\n".join(out) + "\n\n"


def append_block(text, path=OFFSETS):
    with open(path, "a", newline="\n") as f:
        f.write("\n" + text)
