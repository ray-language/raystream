#!/usr/bin/env python3
"""Banco de pruebas de raystream: concurrencia, rangos y RSS del servidor."""
import http.client, subprocess, sys, threading, time

HOST, PORT = "127.0.0.1", 8080
ITEM = sys.argv[1]
PID = subprocess.check_output(["pgrep", "-f", "raystream-native"]).split()[0].decode()


def rss_kb():
    out = subprocess.run(["ps", "-o", "rss=", "-p", PID], capture_output=True, text=True).stdout.strip()
    return int(out) if out else 0


class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.peak = rss_kb()
        self.stop = False

    def run(self):
        while not self.stop:
            self.peak = max(self.peak, rss_kb())
            time.sleep(0.01)


def fetch(path, headers=None):
    c = http.client.HTTPConnection(HOST, PORT, timeout=300)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    n = 0
    while True:
        chunk = r.read(1 << 16)
        if not chunk:
            break
        n += len(chunk)
    c.close()
    return r.status, n


def parallel(n, fn):
    errors = []
    total = [0]
    lock = threading.Lock()

    def work():
        try:
            _, got = fn()
            with lock:
                total[0] += got
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=work) for _ in range(n)]
    sampler = Sampler()
    sampler.start()
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - t0
    sampler.stop = True
    return elapsed, total[0], sampler.peak, errors


print(f"servidor pid {PID} · RSS en reposo {rss_kb()/1024:.0f} MB\n")

print("A) descarga completa en paralelo (fichero de 256 MB)")
for n in (1, 4, 16, 32):
    el, got, peak, errs = parallel(n, lambda: fetch(f"/media/{ITEM}"))
    mb = got / 1048576
    print(f"   {n:2d} clientes: {el:6.2f}s · {mb/el:7.0f} MB/s agregados · "
          f"{mb/el/n:6.0f} MB/s por cliente · RSS pico {peak/1024:.0f} MB"
          + (f" · ERRORES {errs[:2]}" if errs else ""))

print("\nB) peticiones pequeñas con Range (lo que hace un reproductor al saltar)")
for n in (1, 16, 64):
    el, got, peak, errs = parallel(
        n, lambda: fetch(f"/media/{ITEM}", {"Range": "bytes=1000000-1065535"}))
    print(f"   {n:2d} en paralelo: {el*1000:7.1f}ms total · {n/el:7.0f} req/s · "
          f"RSS pico {peak/1024:.0f} MB" + (f" · ERRORES {errs[:2]}" if errs else ""))

print("\nC) 300 peticiones Range secuenciales (coste por petición, conexión nueva cada vez)")
t0 = time.time()
for _ in range(300):
    fetch(f"/media/{ITEM}", {"Range": "bytes=0-65535"})
el = time.time() - t0
print(f"   {el*1000/300:.2f} ms por petición · {300/el:.0f} req/s")

print("\nD) API JSON (pasa por el actor del catálogo)")
t0 = time.time()
for _ in range(300):
    fetch("/api/library")
el = time.time() - t0
print(f"   {el*1000/300:.2f} ms por petición · {300/el:.0f} req/s")

print(f"\nRSS final {rss_kb()/1024:.0f} MB")
