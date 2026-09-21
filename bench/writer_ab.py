#!/usr/bin/env python3
"""A/B controlado de los dos escritores.

Para cada modo: proceso NUEVO, se mide el RSS en reposo, se lanzan N descargas completas
en paralelo muestreando el RSS cada 5 ms, y se reporta el delta sobre el reposo. Se repite
varias veces y se toma la mediana, porque una sola muestra de RSS es ruidosa.

  python3 bench/writer_ab.py <binario writer_ab> <fichero> [clientes] [repeticiones]
"""
import http.client, statistics, subprocess, sys, threading, time

BINARY = sys.argv[1]
FILE = sys.argv[2]
CLIENTS = int(sys.argv[3]) if len(sys.argv) > 3 else 32
ROUNDS = int(sys.argv[4]) if len(sys.argv) > 4 else 3
PORT = 8099


def rss_kb(pid):
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return int(out) if out else 0


def download():
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=300)
    c.request("GET", "/f")
    r = c.getresponse()
    n = 0
    while True:
        b = r.read(1 << 16)
        if not b:
            break
        n += len(b)
    c.close()
    return n


def one_round(mode):
    proc = subprocess.Popen([BINARY, mode, FILE, str(PORT)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(200):                      # esperar a que escuche
            try:
                download_head()
                break
            except OSError:
                time.sleep(0.05)
        time.sleep(0.3)
        idle = rss_kb(proc.pid)

        peak = [idle]
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                peak[0] = max(peak[0], rss_kb(proc.pid))
                time.sleep(0.005)

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        total = [0]
        lock = threading.Lock()

        def work():
            got = download()
            with lock:
                total[0] += got

        threads = [threading.Thread(target=work) for _ in range(CLIENTS)]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - t0
        stop.set()
        sampler.join(timeout=1)
        return idle, peak[0], total[0] / 1048576 / elapsed
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def download_head():
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=2)
    c.request("HEAD", "/f")
    c.getresponse().read()
    c.close()


print(f"{CLIENTS} clientes · {ROUNDS} repeticiones · proceso nuevo por medida\n")
print(f"{'modo':8} {'reposo':>9} {'pico':>9} {'delta':>9} {'delta/conex.':>13} {'caudal':>12}")
for mode in ("direct", "package"):
    idles, peaks, deltas, rates = [], [], [], []
    for _ in range(ROUNDS):
        idle, peak, rate = one_round(mode)
        idles.append(idle); peaks.append(peak); deltas.append(peak - idle); rates.append(rate)
        time.sleep(0.5)
    med = statistics.median
    print(f"{mode:8} {med(idles)/1024:8.1f}M {med(peaks)/1024:8.1f}M {med(deltas)/1024:8.1f}M "
          f"{med(deltas)/CLIENTS:12.0f}K {med(rates):10.0f} MB/s")
    print(f"{'':8} deltas por ronda: {[round(d/1024) for d in deltas]} MB · "
          f"caudales: {[round(r) for r in rates]} MB/s")
