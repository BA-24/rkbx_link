import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pylib"))
from heapscan import blocks
pat = sys.argv[1].encode()
for addr, b in blocks():
    i = b.find(pat)
    while i != -1:
        print(f"{addr+i:X}: {b[i:i+120].split(b'\0',1)[0]!r}")
        i = b.find(pat, i + 1)
