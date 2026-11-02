"""Print OSC messages arriving on 127.0.0.1:4460 for N seconds (addresses + raw args)."""
import socket, sys, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("127.0.0.1", 4460))
s.settimeout(0.5)
end, seen = time.time() + float(sys.argv[1]), {}
while time.time() < end:
    try:
        data, _ = s.recvfrom(4096)
    except socket.timeout:
        continue
    addr = data.split(b"\0", 1)[0].decode()
    seen[addr] = seen.get(addr, 0) + 1
    if "trigger" in addr:
        print(f"{time.time():.3f} {addr}", flush=True)
print("counts:", seen)
