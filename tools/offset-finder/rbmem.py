"""Read-only access to a running rekordbox.exe (Windows): process attach,
memory reads, module sections, heap regions, version detection, and
rkbx_link pointer-chain resolution."""
import ctypes
import ctypes.wintypes as wt
import os
import re
import struct
import sys

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
ver = ctypes.WinDLL("version")

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


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_ulonglong), ("AllocationBase", ctypes.c_ulonglong),
                ("AllocationProtect", wt.DWORD), ("PartitionId", wt.WORD),
                ("RegionSize", ctypes.c_ulonglong), ("State", wt.DWORD),
                ("Protect", wt.DWORD), ("Type", wt.DWORD)]


k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.OpenProcess.restype = wt.HANDLE
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]
k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t


class Rekordbox:
    def __init__(self, name="rekordbox.exe"):
        self.pid = self._find_pid(name)
        self.base, self.size, self.exe_path = self._find_module(name)
        self.h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, self.pid)
        if not self.h:
            sys.exit("could not open rekordbox.exe for reading (run as the same user, not elevated?)")
        self.version = file_version(self.exe_path)

    @staticmethod
    def _find_pid(name):
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        e = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == name:
                k32.CloseHandle(snap)
                return e.th32ProcessID
            ok = k32.Process32NextW(snap, ctypes.byref(e))
        k32.CloseHandle(snap)
        sys.exit("rekordbox.exe is not running")

    def _find_module(self, name):
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, self.pid)
        e = MODULEENTRY32W(dwSize=ctypes.sizeof(MODULEENTRY32W))
        ok = k32.Module32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szModule.lower() == name:
                k32.CloseHandle(snap)
                return e.modBaseAddr, e.modBaseSize, e.szExePath
            ok = k32.Module32NextW(snap, ctypes.byref(e))
        k32.CloseHandle(snap)
        sys.exit("rekordbox.exe module not found")

    # -- raw reads --------------------------------------------------------
    def read(self, addr, n):
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, n, ctypes.byref(got)) \
                or got.value != n:
            return None
        return buf.raw

    def u64(self, addr):
        b = self.read(addr, 8)
        return None if b is None else struct.unpack("<Q", b)[0]

    def is_static(self, addr):
        return self.base <= addr < self.base + self.size

    # -- rkbx_link chain semantics -----------------------------------------
    def resolve(self, chain):
        """chain = [o0, .., on, final]: addr = base; addr = *(addr + o) for each o; + final."""
        addr = self.base
        for off in chain[:-1]:
            if not plausible_ptr(addr):
                return None
            addr = self.u64(addr + off)
            if addr is None:
                return None
        return addr + chain[-1]

    def value(self, chain, fmt):
        a = self.resolve(chain)
        if a is None:
            return None
        b = self.read(a, struct.calcsize("<" + fmt))
        return None if b is None else struct.unpack("<" + fmt, b)[0]

    def cstr(self, chain, n=500):
        a = self.resolve(chain)
        if a is None:
            return None
        # strings may sit near the end of a page; shrink until readable
        while n >= 16:
            b = self.read(a, n)
            if b is not None:
                return b.split(b"\0", 1)[0]
            n //= 2
        return None

    # -- memory layout ----------------------------------------------------
    def sections(self):
        hdr = self.read(self.base, 0x1000)
        pe = struct.unpack_from("<I", hdr, 0x3C)[0]
        nsec = struct.unpack_from("<H", hdr, pe + 6)[0]
        optsz = struct.unpack_from("<H", hdr, pe + 20)[0]
        off = pe + 24 + optsz
        for _ in range(nsec):
            name = hdr[off:off + 8].rstrip(b"\0").decode(errors="replace")
            vsize, va = struct.unpack_from("<II", hdr, off + 8)
            chars = struct.unpack_from("<I", hdr, off + 36)[0]
            yield name, va, vsize, chars
            off += 40

    def writable_sections(self):
        """Yield (module offset, bytes) for each writable section of rekordbox.exe."""
        for _, va, vsize, chars in self.sections():
            if not chars & 0x80000000:
                continue
            data = bytearray()
            for chunk in range(0, vsize, 0x10000):
                n = min(0x10000, vsize - chunk)
                data += self.read(self.base + va + chunk, n) or b"\0" * n
            yield va, bytes(data)

    def static_pointers(self):
        """Yield (module offset G, qword at base+G) for plausible pointers in writable sections."""
        for va, data in self.writable_sections():
            for i in range(0, len(data) - 7, 8):
                v = struct.unpack_from("<Q", data, i)[0]
                if plausible_ptr(v):
                    yield va + i, v

    def regions(self):
        """Committed read-write private memory, plus the module's own writable pages."""
        addr, mbi = 0, MBI()
        while k32.VirtualQueryEx(self.h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            rw = mbi.Protect & 0xFF in (0x04, 0x08, 0x40) and not mbi.Protect & 0x100
            if mbi.State == 0x1000 and rw and (mbi.Type == 0x20000 or self.is_static(mbi.BaseAddress)):
                yield mbi.BaseAddress, mbi.RegionSize
            addr = mbi.BaseAddress + mbi.RegionSize
            if addr >= 0x7FFFFFFFFFFF:
                break

    def blocks(self, chunk=64 << 20):
        for start, length in self.regions():
            for off in range(0, length, chunk):
                b = self.read(start + off, min(chunk, length - off))
                if b:
                    yield start + off, b

    def find_bytes(self, pat, align=1):
        hits = []
        for addr, b in self.blocks():
            i = b.find(pat)
            while i != -1:
                if (addr + i) % align == 0:
                    hits.append(addr + i)
                i = b.find(pat, i + 1)
        return hits


def plausible_ptr(v):
    return v is not None and 0x10000 <= v < 0x7FFFFFFFFFFF


def file_version(path):
    """Product version of an exe from its version resource, falling back to the
    install folder name ("rekordbox 7.2.19")."""
    size = ver.GetFileVersionInfoSizeW(path, None)
    if size:
        buf = ctypes.create_string_buffer(size)
        if ver.GetFileVersionInfoW(path, 0, size, buf):
            p, n = ctypes.c_void_p(), wt.UINT()
            if ver.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(n)) and n.value >= 52:
                ffi = ctypes.string_at(p.value, n.value)
                ms, ls = struct.unpack_from("<II", ffi, 16)  # dwProductVersionMS/LS
                v = (ms >> 16, ms & 0xFFFF, ls >> 16)
                if any(v):
                    return ".".join(map(str, v))
    m = re.search(r"(\d+\.\d+\.\d+)", os.path.basename(os.path.dirname(path)))
    return m.group(1) if m else None


def fmt_chain(chain):
    return " ".join(f"{x:08X}" if i == 0 and len(chain) > 1 else f"{x:X}" for i, x in enumerate(chain))


def parse_chain(s):
    return [int(x, 16) for x in s.split()]
