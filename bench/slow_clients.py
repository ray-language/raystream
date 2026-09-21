# Clientes lentos: el caso clásico que tumba un servidor de medios mal hecho.
import http.client, subprocess, sys, threading, time
ITEM = sys.argv[1]
PID = subprocess.check_output(["pgrep","-f","raystream-native"]).split()[0].decode()
rss = lambda: int(subprocess.run(["ps","-o","rss=","-p",PID],capture_output=True,text=True).stdout.strip() or 0)

stop = False
def slow_reader():
    c = http.client.HTTPConnection("127.0.0.1", 8080, timeout=60)
    c.request("GET", f"/media/{ITEM}")
    r = c.getresponse()
    read = 0
    while not stop and read < 2_000_000:
        b = r.read(16384)          # 16 KB cada 200 ms ≈ 80 KB/s
        if not b: break
        read += len(b)
        time.sleep(0.2)
    c.close()

print(f"RSS antes: {rss()/1024:.0f} MB")
threads = [threading.Thread(target=slow_reader, daemon=True) for _ in range(24)]
for t in threads: t.start()
time.sleep(2)
print(f"RSS con 24 clientes lentos en vuelo: {rss()/1024:.0f} MB")

# Mientras tanto, ¿un cliente normal sigue siendo atendido rápido?
t0 = time.time()
c = http.client.HTTPConnection("127.0.0.1", 8080, timeout=30)
c.request("GET", "/api/stats"); c.getresponse().read(); c.close()
print(f"latencia de /api/stats con la cola ocupada: {(time.time()-t0)*1000:.1f} ms")

t0 = time.time()
c = http.client.HTTPConnection("127.0.0.1", 8080, timeout=30)
c.request("GET", f"/media/{ITEM}", headers={"Range":"bytes=0-65535"})
n = len(c.getresponse().read()); c.close()
print(f"un Range de 64 KB tarda {(time.time()-t0)*1000:.1f} ms ({n} octetos)")

stop = True
for t in threads: t.join(timeout=5)
time.sleep(1)
print(f"RSS después: {rss()/1024:.0f} MB")
