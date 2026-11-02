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

The whole process takes about 10 minutes and a few clicks in Rekordbox.

1. Load **different** tracks on deck 1 and deck 2, keep the tempo/pitch at
   **0%**, start deck 1 **playing** and leave deck 2 paused.
2. `python rbfind.py new`
   - It finds the ANLZ path, BPM and sample position on its own, starting from
     the chain shapes of the closest known version (seconds).
   - It finds track info by walking back from the `Track Title:` strings in
     memory (a few minutes). This gives hundreds of candidates.
   - For the master deck index, it asks you to click **MASTER** on deck 1 or 2
     a few times.
3. Quit and restart Rekordbox, load two other tracks on decks 1 and 2, then
   run `python rbfind.py confirm`. The restart removes the candidate chains
   that only worked by chance. You'll be asked to toggle MASTER twice more.
   `confirm` then prints the new block, appends it to `data/offsets` if you
   agree, and runs `check`.
4. Set `keeper.rekordbox_version` in `config`, test with rkbx_link, and commit
   `data/offsets`.

Progress is saved in `work/<version>/state.json`, so a step can be re-run on
its own. For example, `new --skip-track` reuses the track candidates and only
redoes the other steps.

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

How the chains changed between the two versions in `data/offsets`:

| field | 7.2.2 | 7.2.19 |
|---|---|---|
| BPM | `0564B038 0 2B0 1A0` | `05E09790 0 2C0 1A0` |
| position | BPM chain, final `130` | BPM chain, final `130` |
| ANLZ | `057696B8 8 3F0 0` | `05F53DA8 8 3F0 0` |
| track info | `05737C48 20 410 80 168 F0 0` | `061A82C0 3A0 478 340 CB0 100 0` |
| master | `05737C48 20 278 124` | `061A82C0 5A0 C94` |

BPM, position and ANLZ keep their shape, so `new` finds them on its own. Track
info and master change shape, which is why those two steps search more widely
and need the restart.

## Modules

- `rbfind.py`: the command-line tool above
- `rbmem.py`: process access, memory reads, version detection, chain resolution
- `scan.py`: search strategies (shape search, deck-pair back-walk, master toggles)
- `anlz.py`: ANLZ beat grid reader
- `offsets_file.py`: reads and writes `data/offsets`
- `oscmon.py`: OSC output monitor
