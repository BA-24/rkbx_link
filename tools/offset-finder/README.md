# offset-finder

Python scripts used to find the Windows memory offsets for a new Rekordbox
version (used for 7.2.19). They only read Rekordbox's memory (`ReadProcessMemory`), never write to it.

Run them with **Windows** Python (`python.exe`) while Rekordbox is open in
Performance mode. `heapscan.py` and everything built on it needs numpy:

```
python -m pip install --target tools/pylib numpy
```

## Offsets format

Each pointer line is `G o1 .. on F`: read the qword at `rekordbox.exe base + G`,
then repeatedly at `previous + o`, then add `F`. Per version: master deck index
(u8), then for each deck BPM (f32), sample position (i64, at 44.1 kHz), track
info string (`Track Title: ..\nArtist: ..\nAlbum: ..`) and ANLZ path string.

## Procedure

1. **Same shape, new global.** Load tracks on decks 1-2, then run
   `rbscan.py 2`. It keeps the previous version's chain shapes and searches the
   module's writable sections for the new global offset. For 7.2.19 this found
   the ANLZ chain directly.
2. **BPM / sample position.** Play deck 1, pause deck 2, then run
   `playfilter.py <deck1 bpm>`. It keeps only BPM floats that have a sample
   counter advancing at about 44.1 kHz nearby (sample position was BPM - 0x70).
   Then run `bpmchains.py <deck2 bpm> <hit addrs..>` and look for a chain pair
   differing by a deck stride of 8. Check the result with `verify.py`
   (edit the chain in its `__main__`).
3. **Track info.** Find the strings with `findstr.py "Track Title: "`, then run
   `pairwalk.py <deck1 addr> <deck2 addr> > track_candidates.txt`. Restart
   Rekordbox, load *different* tracks, and run
   `filtertrack.py track_candidates.txt track_pass2.txt`. The deck stride is the
   4th-from-last offset.
4. **Master deck index.** This step needs `track_pass2.txt`, since it searches
   below those globals. With deck 2 as master, run `master2.py init 1`. Toggle
   master and run `master2.py filter 0`, toggle back and run `master2.py filter 1`,
   and repeat until a few candidates remain.
5. Add the block to `data/offsets` and set `keeper.rekordbox_version`. To check
   the output, use `osclisten.py 20` (beat trigger timing) or `oscdump.py 8`
   (last value per OSC address).

## 7.2.19 notes

- Decks 1-2 were verified across a restart. Decks 3-4 are extrapolated by the
  stride and untested.
- Master index had three equivalent candidates rooted at `061A82C0`
  (`5A0 C94`, `5A8 A04`, `620 774`). `5A0 C94` is used.
