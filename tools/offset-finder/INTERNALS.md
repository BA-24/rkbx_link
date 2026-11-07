# How rkbx_link finds the beat, and what has broken

Notes for whoever debugs this next. They cover what rkbx_link reads from
Rekordbox, how Rekordbox 7.2.19 stores it, the failures found so far and how
they were tracked down, and ideas that haven't been tried. Everything here was
observed on Windows, Rekordbox 7.2.19, 2-deck Performance mode, unless it says
otherwise.

## From memory to a beat

rkbx_link never sees a beat in Rekordbox's memory. It works one out:

1. **Which deck:** the master deck index (one byte).
2. **Where in the track:** that deck's sample position (i64, 44.1 kHz samples).
3. **Which beat grid:** that deck's ANLZ path, a `.DAT` file on disk. rkbx_link
   reads the file's beat grid (`PQTZ`: one entry per beat with its time and
   tempo) and watches the file for edits.
4. **The beat:** it finds the last grid beat before the position and sends
   `/master/beat/trigger/1` each time the beat number wraps.

So the pulses are only as right as the grid file. If rkbx_link has the wrong
file, it keeps pulsing confidently at the wrong tempo and phase. That is the
worst failure, because nothing in its output looks wrong.

The BPM chain doesn't affect beats. Beats come from the grid alone.

## Rekordbox 7.2.19 layout

### Deck track objects (ANLZ path and UUID)

`*(rekordbox.exe + 05F53DA8)` points to a block of track objects:

| offset | holds |
|---|---|
| `+0` | vtable (static) |
| `+8`, `+10`, `+18`, `+20` | current track of decks 1-4 |
| `+28` | where a track lands first while it loads (seen in 2-deck mode) |
| `+38` .. `+58` | the previous track of each deck |

Loading a track onto deck 2 went `+28` = new track, then `+10` = new track and
`+40` = the track it replaced, within about 2 s.

In each track object:

| field | holds |
|---|---|
| `+10` | pointer to the track UUID string, e.g. `82ec46a5-6d01-4609-806d-18872c89a613` |
| `+3F0` | pointer to the ANLZ path string, `C:/Users/<you>/AppData/Roaming/Pioneer/rekordbox/share/PIONEER/USBANLZ/82e/c46a5-.../ANLZ0000.DAT` |

So the chains are `05F53DA8 <deck> 3F0 0` (ANLZ path) and
`05F53DA8 <deck> 10 0` (UUID). The UUID gives the ANLZ folder:
`USBANLZ/<first 3 chars>/<the rest>/`.

**The ANLZ path stays empty when Rekordbox analyses the track while loading
it** (a track you just added, or one never analysed before). The UUID is set
either way. See "Off-beat pulses" below.

### Player objects (BPM, position)

`*(*(rekordbox.exe + 05E09790) + <deck>) + 2C0` is a per-deck player object:
BPM (f32) at `+1A0`, sample position (i64) at `+130`. These stay stable across
restarts and track loads. The BPM field follows a dynamic beat grid, and it
jitters by about ±0.6 even on a stopped deck. That's harmless, because beats
don't use it.

### Master index

`05F19B48 20 0 280 124`, one byte. It is rekordbox's MASTER deck (0-based),
which is **not the same as the playing deck**: MASTER stays on a deck after
you stop it.

## Failures found (2026-11-07) and their fixes

### Off-beat pulses after loading a new track

Symptoms: the beat grid on screen looked right, yet the pulses were steady but
off-beat. It started after mixing into a track that had been added that day.

Cause: that track was analysed as it loaded, so its deck's ANLZ path stayed
empty. rkbx_link logged a read error and kept the **previous** track's grid.
Reproduced on purpose: a new track loaded at 17:26:55, its `ANLZ0000.DAT` was
written at 17:26:58, and its deck's path stayed empty while it played. Tracks
that were analysed before loading always got their path at once.

Fixes:

- `d66d116`: an empty or unreadable path now clears the old grid, so nothing
  is sent instead of wrong beats.
- `7fbee8c`: when the path is empty, the path is rebuilt from the track UUID
  (`#uuid` lines in `data/offsets`, `track_uuid` in `src/offsets.rs`). It is
  used only once the file exists, so a track still being analysed picks up
  its grid when the analysis finishes.

### No beats while another deck plays

Symptoms: one deck playing with MASTER left on a stopped deck. rkbx_link
followed the stopped one.

Fix (`058744c`): a deck counts as playing if its position changed in the last
300 ms. If the MASTER deck is stopped and another deck is playing, rkbx_link
follows the playing deck and sticks with it while it plays. A playing MASTER
deck always wins, so cueing a track in on another deck doesn't take over.

## How to investigate the next one

Tools in this folder:

- `decks.py [seconds]` prints, per deck: playing or stopped, position, BPM,
  ANLZ path (or `EMPTY`) and UUID, plus whether the UUID's grid file exists.
  Leave it running while you reproduce a problem; it prints on every change.
- `oscmon.py` shows what rkbx_link actually sends.
- `app.debug true` in rkbx_link's `config` logs `Deck N ANLZ file path
  changed: ...`, so you can see which grid file it uses. Decks are 0-based
  there.
- `scan.back_to_static(rb, [addr], ...)` finds static chains to an address;
  `scan.meet_in_middle` joins a forward walk from a known object to a back-walk
  from a target.

Lessons from the first round:

- **Identify the right grid file by the UUID, not by tempo.** The deck's BPM
  display shows the current tempo (pitch, sync, dynamic grids), and dynamic
  grids drift (Endless Fantasy: first beat 170.89, median 170.03, display
  167.60). Guessing the file by tempo picked the wrong one and wasted the live
  broken state. With the UUID chain, read the deck's UUID and go straight to
  its folder. Without it: file creation time (analysed when?), the number of
  beats and the last beat's time against the track length.
- **Keep the broken state.** Don't reload the deck until you have looked at
  it. To find where a value lives, search memory for it while it is right on
  one deck and wrong or empty on another.
- **Pair decks to find a chain.** Search back from deck 2's string, then keep
  only chains that give deck 1's string when the deck offset is moved by 8.
  This rules out the many chains that run through caches and history lists.
  Hits ending in a nonzero final offset point into the middle of a big buffer
  and are almost always noise.
- **Know which slot you're looking at.** The `+38` .. `+58` slots hold the
  previous tracks, which looked like the wrong grid for the current track at
  first.

Finding the UUID chain for a new version (rbfind doesn't do this yet):

1. Load different tracks on decks 1 and 2.
2. Read one deck's ANLZ path. The UUID is the two folder names joined:
   `USBANLZ/82e/c46a5-...` gives `82ec46a5-...`.
3. `rb.find_bytes(uuid.encode() + b"\0")` for each deck. Each was in memory
   exactly once on 7.2.19.
4. `back_to_static` from deck 2's UUID address, then keep the chains that
   resolve to deck 1's address with the deck offset moved by -8. The one that
   shares the ANLZ chain's prefix (`05F53DA8 <deck>`) is the one to use.
5. Add `#uuid <chain>` after each deck's ANLZ line.

## Ideas not tried yet

- **Streaming tracks** (SoundCloud, Beatport, Beatsource, TIDAL; Rekordbox
  doesn't stream Spotify) are analysed as they load, so they hit the empty
  path case. Unknown: whether their grid also lands in `USBANLZ/<uuid>`, and
  if not, where. Test: load a streamed track you've never played, then run
  `decks.py` and look for new `ANLZ0000.DAT` files.
- **USB sticks:** exported tracks are analysed during export, so their path
  should be set and the UUID fallback shouldn't be needed. Their grid folders
  are named like `PIONEER/USBANLZ/P016/0000875E`, not by UUID, so the fallback
  wouldn't find them anyway. Untested.
- **Read the grid from memory instead of files.** Rekordbox draws the grid, so
  it is in memory. Reading it there would work for every source, at the cost
  of more chains to find per version. Worth it only if some source turns out
  to have no usable file.
- **Decks 3-4:** UUID chains are extrapolated by the deck offset (`18`, `20`),
  like the rest of 7.2.19's decks 3-4. Untested.
- **rbfind:** have `new`/`confirm` find and check the UUID chain (steps above).

## Driving the Rekordbox UI from scripts

Used for unattended tests. What worked and what didn't on 2026-11-07
(2560x1440 screen at 125% scaling, script not DPI-aware, so coordinates were
in 2048x1280):

- Dragging a track row onto a deck worked with real mouse-move events
  (`mouse_event` with `MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE`), not with
  `SetCursorPos` alone.
- Drags failed while the search box had text and focus.
- Clicks on buttons and column headers were ignored, though clicks on rows
  selected them. On 2026-11-03 the same kind of clicks (from a DPI-aware
  script, physical coordinates) did work, so DPI awareness is the first thing
  to try.
