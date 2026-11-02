"""Minimal rekordbox ANLZ (.DAT) beat grid reader (PQTZ tag, big-endian).
See https://djl-analysis.deepsymmetry.org/rekordbox-export-analysis/anlz.html"""
import struct


def beat_grid(path):
    """Return [(time_ms, bpm)] for every beat, or [] if the file has no grid."""
    try:
        data = open(path, "rb").read()
    except OSError:
        return []
    if data[:4] != b"PMAI":
        return []
    off = struct.unpack_from(">I", data, 4)[0]
    while off + 12 <= len(data):
        tag = data[off:off + 4]
        len_header, len_tag = struct.unpack_from(">II", data, off + 4)
        if len_tag < 12:
            break
        if tag == b"PQTZ":
            n = struct.unpack_from(">I", data, off + 20)[0]
            beats = []
            for i in range(n):
                _, tempo, time_ms = struct.unpack_from(">HHI", data, off + len_header + 8 * i)
                beats.append((time_ms, tempo / 100.0))
            return beats
        off += len_tag
    return []


def bpm_at(grid, seconds):
    """Grid tempo at a playback position (seconds)."""
    if not grid:
        return None
    ms = seconds * 1000
    bpm = grid[0][1]
    for t, b in grid:
        if t > ms:
            break
        bpm = b
    return bpm


def tempos(grid):
    return sorted({b for _, b in grid})
