"""Read-only scanner for rkbx_link offsets in a running rekordbox.exe (Windows).

Assumes the pointer chains keep the 7.2.2 shape and searches the module's
writable sections for the new global (first) offset of each chain.
"""
import ctypes
import ctypes.wintypes as wt
import struct
import sys

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPPROCESS = 0x2
TH32CS_SNAPMODULE = 0x8 | 0x10


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("th32ModuleID", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("GlblcntUsage", wt.DWORD), ("ProccntUsage", wt.DWORD),
                ("modBaseAddr", ctypes.c_void_p), ("modBaseSize", wt.DWORD),
                ("hModule", wt.HMODULE), ("szModule", ctypes.c_wchar * 256),
                ("szExePath", ctypes.c_wchar * 260)]


k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.OpenProcess.restype = wt.HANDLE
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]


def find_process(name):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    e = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        if e.szExeFile.lower() == name:
            k32.CloseHandle(snap)
            return e.th32ProcessID
        ok = k32.Process32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    sys.exit("rekordbox.exe not running")


def find_module(pid, name):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, pid)
    e = MODULEENTRY32W(dwSize=ctypes.sizeof(MODULEENTRY32W))
    ok = k32.Module32FirstW(snap, ctypes.byref(e))
    while ok:
        if e.szModule.lower() == name:
            k32.CloseHandle(snap)
            return e.modBaseAddr, e.modBaseSize
        ok = k32.Module32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    sys.exit("module not found")


pid = find_process("rekordbox.exe")
base, size = find_module(pid, "rekordbox.exe")
h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)


def read(addr, n):
    buf = ctypes.create_string_buffer(n)
    got = ctypes.c_size_t()
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, n, ctypes.byref(got)) or got.value != n:
        return None
    return buf.raw


def u64(addr):
    b = read(addr, 8)
    return None if b is None else struct.unpack("<Q", b)[0]


def plausible_ptr(v):
    return 0x10000 <= v < 0x7FFFFFFFFFFF


def follow(first_val, inner, final):
    """first_val = qword stored at base+G; then rkbx_link's chain semantics."""
    addr = first_val
    for off in inner:
        if not plausible_ptr(addr):
            return None
        addr = u64(addr + off)
        if addr is None:
            return None
    if not plausible_ptr(addr):
        return None
    return addr + final


def cstr(addr, n):
    b = read(addr, n)
    if b is None:
        return None
    return b.split(b"\0", 1)[0]


def sections():
    hdr = read(base, 0x1000)
    pe = struct.unpack_from("<I", hdr, 0x3C)[0]
    nsec = struct.unpack_from("<H", hdr, pe + 6)[0]
    optsz = struct.unpack_from("<H", hdr, pe + 20)[0]
    off = pe + 24 + optsz
    for i in range(nsec):
        name = hdr[off:off + 8].rstrip(b"\0").decode()
        vsize, va = struct.unpack_from("<II", hdr, off + 8)
        chars = struct.unpack_from("<I", hdr, off + 36)[0]
        yield name, va, vsize, chars
        off += 40


def candidate_globals():
    for name, va, vsize, chars in sections():
        if not chars & 0x80000000:  # IMAGE_SCN_MEM_WRITE
            continue
        data = b""
        for chunk in range(0, vsize, 0x10000):
            part = read(base + va + chunk, min(0x10000, vsize - chunk))
            data += part if part else b"\0" * min(0x10000, vsize - chunk)
        for i in range(0, len(data) - 7, 8):
            v = struct.unpack_from("<Q", data, i)[0]
            if plausible_ptr(v):
                yield va + i, v


def deck_str(v, inner, final, n):
    a = follow(v, inner, final)
    return None if a is None else cstr(a, n)


def check_anlz(v, k):
    s = deck_str(v, [8 + 8 * k, 0x3F0], 0, 500)
    return s if s and s.endswith(b".DAT") and b"ANLZ" in s else None


def check_track(v, k):
    s = deck_str(v, [0x20, 0x410, 0x80 + 8 * k, 0x168, 0xF0], 0, 200)
    return s if s and b": " in s and b"\n" in s else None


def check_bpm(v, k):
    a = follow(v, [8 * k, 0x2B0], 0x1A0)
    b = a and read(a, 4)
    if not b:
        return None
    bpm = struct.unpack("<f", b)[0]
    return bpm if 40.0 <= bpm <= 300.0 else None


def main():
    decks = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    print(f"rekordbox.exe pid={pid} base={base:#x} size={size:#x}")
    found = {"anlz": [], "track": [], "bpm": []}
    checks = {"anlz": check_anlz, "track": check_track, "bpm": check_bpm}
    for g, v in candidate_globals():
        for key, fn in checks.items():
            hits = {k: r for k in range(decks) if (r := fn(v, k)) is not None}
            if hits:
                found[key].append((g, hits))
                print(f"{key:6s} G={g:08X} {hits}", flush=True)
    for key, lst in found.items():
        print(f"== {key}: {len(lst)} candidate(s)")


if __name__ == "__main__":
    main()
