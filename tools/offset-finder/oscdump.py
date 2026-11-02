"""Dump decoded OSC messages (address, first float arg) on 127.0.0.1:4460 for N seconds."""
import socket, struct, sys, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("127.0.0.1", 4460)); s.settimeout(0.5)
end = time.time() + float(sys.argv[1]); last = {}
def pad(n): return (n + 4) & ~3
while time.time() < end:
    try: d, _ = s.recvfrom(4096)
    except socket.timeout: continue
    a = d.split(b"\0", 1)[0]; i = pad(len(a)); tags = d[i:].split(b"\0", 1)[0]; j = i + pad(len(tags))
    v = struct.unpack(">f", d[j:j+4])[0] if tags[1:2] == b"f" else d[j:].split(b"\0", 1)[0]
    last[a.decode()] = v
for k in sorted(last): print(k, last[k])
