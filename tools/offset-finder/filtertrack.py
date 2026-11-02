"""filtertrack.py in.txt out.txt: keep chains resolving to a 'Track Title:' string on both decks."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify import val

out = []
for line in open(sys.argv[1]):
    if not line.startswith("STATIC"):
        continue
    ch = [int(x, 16) for x in line.split()[1:]]
    res = []
    for k in range(2):
        c = list(ch)
        c[-4] += 8 * k
        s = val(c, "s", 200)
        res.append(s.split(b"\n")[0].decode(errors="replace") if s and s.startswith(b"Track Title: ") else None)
    if all(res):
        out.append(line)
        print(line.split(None, 1)[1].strip(), "|", res)
open(sys.argv[2], "w").writelines(out)
print(f"{len(out)} kept", file=sys.stderr)
