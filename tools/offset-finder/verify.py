import os, struct, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rbscan import base, read, u64

def resolve(chain):
    addr = base
    for off in chain[:-1]:
        addr = u64(addr + off)
        if addr is None:
            return None
    return addr + chain[-1]

def val(chain, fmt, n=None):
    a = resolve(chain)
    if a is None:
        return None
    if fmt == "s":
        b = read(a, n)
        return b and b.split(b"\0", 1)[0]
    b = read(a, struct.calcsize(fmt))
    return b and struct.unpack("<" + fmt, b)[0]

if __name__ == "__main__":
    G = 0x5E09790
    for _ in range(2):
        for k in range(2):
            bpm = val([G, 8 * k, 0x2C0, 0x1A0], "f")
            pos = val([G, 8 * k, 0x2C0, 0x130], "q")
            print(f"deck{k+1}: bpm={bpm} pos={pos} ({pos/44100 if pos else 0:.1f}s)")
        time.sleep(1)
