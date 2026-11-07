# offset-finder

Tools for checking rkbx_link offsets and for finding them for a new Rekordbox
release. They only read Rekordbox's memory (`ReadProcessMemory`), never write to it.

## Setup (once)

Install [Python 3](https://www.python.org/downloads/) for **Windows**. Then,
from the repo root, install numpy into a local folder (nothing is installed
system-wide):

```
python -m pip install --target tools/pylib numpy
```

Run every command below from `tools/offset-finder` while Rekordbox is open in
**Performance** mode.

## Is my version supported?

```
python rbfind.py version     # which Rekordbox is running, and whether data/offsets has it
python rbfind.py check       # live-check every field against Rekordbox
```

`check` works best with a different track on each deck and one deck playing.
It cross-checks the BPM against the track's own beat grid (from its ANLZ file).
A full pass looks like this:

```
  [ok] master deck          deck 1
 deck 1:
  [ok] ANLZ path            C:/Users/.../ANLZ0000.DAT
  [ok] sample position      54962623 (1246.3s)
  [ok] playback rate        44218 samples/s
  [ok] BPM                  175.00 (grid 175.00, pitch +0.00%)
  [ok] track info           'S3RL Penultimately Presents'
```

## Adding a new Rekordbox version

The whole process takes about 20 minutes, with a few clicks in Rekordbox and
two restarts.

1. Load **different** tracks on deck 1 and deck 2, keep the tempo/pitch at
   **0%**, start deck 1 **playing** and leave deck 2 paused.
2. `python rbfind.py new`
   - It finds the ANLZ path, BPM and sample position on its own, starting from
     the chain shapes of the closest known version (seconds).
   - For the master deck index, it asks you to click **MASTER** on deck 1 or
     2 about ten times. It then collects every pointer chain that reaches the
     master byte (about 2 minutes).
   - For track info, it asks you to type the titles on deck 1 and 2, then
     searches for chains to them (about 5 minutes).
3. Quit and restart Rekordbox, load two **other** tracks on decks 1 and 2,
   and run `python rbfind.py confirm`. Each restart removes chains that only
   worked by chance, because they went through objects that Rekordbox
   allocates somewhere else each run. It asks for a few MASTER clicks and the
   new titles again.
4. Do step 3 a second time. A chain can survive one restart and still break
   on the next, so `confirm` only prints the new block after two restarts.
   It appends the block to `data/offsets` if you agree, and runs `check`.
5. Set `keeper.rekordbox_version` in `config`, test with rkbx_link, and commit
   `data/offsets`.

`confirm` checks that Rekordbox was really restarted (by its process start
time) and refuses to run otherwise.

Progress is saved in `work/<version>/state.json`. `new --skip-master` or
`new --skip-track` reuses that step's saved candidates.

### Things to know

- **Track info may get a placeholder.** In 7.2.19 no track info chain
  survived two restarts. The per-deck title strings seem to live in UI
  objects that are rebuilt each run. When nothing survives, `confirm` writes
  the ANLZ path chain on the track info lines. rkbx_link drops its connection
  whenever any chain fails to read, so a broken track chain would stop all
  output, beats included. With the placeholder, the reads succeed and track
  titles are just empty. `check` shows this as `[--] placeholder`.
- **Master chain choice.** Several chains usually survive. `confirm` prefers
  the one shaped like the previous version's master chain. It avoids bytes
  inside one deck's own object: such a byte is a per-deck "is master" flag,
  which matches the index with 2 decks but not with 4.
- **Decks 3 and 4** are extrapolated from the deck 1 to deck 2 difference
  and are untested unless you run in 4-deck mode.

To check that output reaches you, run rkbx_link with OSC enabled and
`python oscmon.py 20`. It shows every beat trigger, the last value per OSC
address, and the measured BPM.

## Offsets format

Each pointer line is `G o1 .. on F`: read the qword at `rekordbox.exe base + G`,
then at `previous + o` for each offset, then add `F`. Per version there is a
master deck index line (u8), then for each deck four lines: BPM (f32), sample
position (i64, in 44.1 kHz samples), track info string
(`Track Title: ..\nArtist: ..\nAlbum: ..`) and ANLZ path string. Decks are
8 bytes apart in some array along each chain.

This fork also reads an optional `#uuid <chain>` line after a deck's ANLZ line:
the loaded track's UUID. When the ANLZ path is empty (Rekordbox leaves it
empty for tracks it analyses while loading), rkbx_link finds the grid file
through the UUID instead. Other rkbx_link builds read the line as a comment.
`rbfind` doesn't find this chain yet; [INTERNALS.md](INTERNALS.md) explains how
to find it by hand.

How the chains changed between the two versions in `data/offsets`:

| field | 7.2.2 | 7.2.19 |
|---|---|---|
| BPM | `0564B038 0 2B0 1A0` | `05E09790 0 2C0 1A0` |
| position | BPM chain, final `130` | BPM chain, final `130` |
| ANLZ | `057696B8 8 3F0 0` | `05F53DA8 8 3F0 0` |
| track info | `05737C48 20 410 80 168 F0 0` | placeholder (none stable) |
| master | `05737C48 20 278 124` | `05F19B48 20 0 280 124` |

BPM, position and ANLZ keep their shape, so `new` finds them on its own.
Master kept most of its shape but gained a level. That's why the master and
track steps search all of memory and rely on restarts to weed out chains.

## When beats go wrong

[INTERNALS.md](INTERNALS.md) describes how Rekordbox 7.2.19 stores the deck
data, the failures found so far (off-beat pulses after loading a new track, no
beats while MASTER sits on a stopped deck), how they were tracked down, and
ideas not tried yet. Start there.

## Modules

- `rbfind.py`: the command-line tool above
- `rbmem.py`: process access, memory reads, version detection, chain resolution
- `scan.py`: search strategies (shape search, back-walks to static memory, page-diff master search, forward/back meet-in-the-middle)
- `anlz.py`: ANLZ beat grid reader
- `offsets_file.py`: reads and writes `data/offsets`
- `oscmon.py`: OSC output monitor
- `decks.py`: live per-deck view (playing, path, UUID) for debugging; see [INTERNALS.md](INTERNALS.md)
