# Banco de pruebas

Mide lo mismo, igual, para poder comparar entre versiones de raylang.

```sh
# 1. Servidor (binario nativo: en la VM los números no son comparables)
ray build --native -o /tmp/raystream && /tmp/raystream --dir media --port 8080 &

# 2. Fichero grande dentro de la biblioteca (no se versiona; bórralo al terminar)
dd if=/dev/urandom of=media/video/bench.mp4 bs=1m count=256

# 3. Su id, y a medir
ID=$(curl -s "localhost:8080/api/library?kind=video" \
     | python3 -c "import json,sys;print([x['id'] for x in json.load(sys.stdin) if x['name']=='bench.mp4'][0])")
python3 bench/throughput.py   $ID
python3 bench/slow_clients.py $ID

rm media/video/bench.mp4
```

`throughput.py` mide descargas completas en paralelo (1/4/16/32 clientes), peticiones pequeñas con
`Range` —lo que hace un reproductor al saltar—, el coste por petición y el de la API JSON, con el
RSS del servidor muestreado cada 10 ms. `slow_clients.py` lanza 24 clientes leyendo a ~80 KB/s y
comprueba que la memoria no crece y que un cliente normal sigue siendo atendido rápido.

## Mediciones

Binario nativo, Mac mini M4 (Mac16,10), loopback, fichero de 256 MB.

| Clientes | 1.26.0 / net 0.2.0 · escritor propio | 1.27.0 / net 0.3.0 · escritor propio | 1.27.0 / net 0.3.0 · `serve_file` | **1.27.1 / net 0.3.1 · `serve_file`** |
|---|---|---|---|---|
| 1 | 3.883 MB/s · 42 MB | 4.259 MB/s · 50 MB | 5.740 MB/s · 24 MB | 3.035 MB/s · 19 MB |
| 4 | 4.825 MB/s · 42 MB | 4.926 MB/s · 55 MB | 4.438 MB/s · 32 MB | 4.275 MB/s · 27 MB |
| 16 | 4.159 MB/s · 44 MB | 4.234 MB/s · 64 MB | 3.783 MB/s · 86 MB | 3.756 MB/s · 45 MB |
| 32 | 4.097 MB/s · 51 MB | 4.107 MB/s · 68 MB | 3.658 MB/s · 144 MB | **3.644 MB/s · 104 MB** |

La última columna es la configuración actual. El salto de memoria del hallazgo 18 está corregido en
net 0.3.1 (cola del productor 1 en vez de 4): a 32 clientes, 104 MB frente a los 144 MB de 0.3.0.
El ~10% de caudal frente al bucle directo es el salto productor→canal→escritor y **no** se arregla
subiendo el trozo: con `serve_file_with(1 MB, cola 1)` el RSS sube a 266 MB y el caudal no se mueve.
La medida con un solo cliente es la más ruidosa de todas (3,0–5,7 GB/s entre ejecuciones).

| Prueba | 1.26.0 | 1.27.0 |
|---|---|---|
| Range pequeño, secuencial | 0,14 ms · 6.975 req/s | 0,14 ms · 6.994 req/s |
| Range pequeño, 64 en paralelo | 6.212 req/s | 6.680 req/s |
| `/api/library` (pasa por el actor) | 0,19 ms · 5.238 req/s | 0,19 ms · 5.244 req/s |
| 24 clientes lentos | RSS 51 → 56 MB · `/api/stats` 1,1 ms | RSS 69 → 73 MB · `/api/stats` 1,1 ms |
| Miniatura PNG 480×480 (primera) | 16 ms nativo · 706 ms VM | 26 ms nativo · 701 ms VM |
| Streaming de 512 MB | RSS 31 → 34 MB | — |

**Lectura**: rendimiento idéntico dentro del ruido (el techo de ~4 GB/s es el loopback, no el
servidor) y latencias iguales. Lo único que se mueve es el **RSS en reposo, 42 → 50 MB**, y el pico
bajo carga, 51 → 68 MB, con el mismo código y la misma carga: unos 17 MB más. No está medido de
dónde salen; candidatos son el runtime nuevo y `net` 0.3.0. Las miniaturas en nativo pasan de 16 a
26 ms (una sola muestra: puede ser ruido).

## Qué esperar de los cambios de raylang

Lo que tocaría estos números, por orden: una primitiva de `sendfile` (hoy cada octeto se copia dos
veces), keep-alive en el bucle de conexión (hoy cada salto del reproductor abre una conexión), y
cualquier mejora del coste de asignación de `bytes` en el runtime. Los hallazgos 2, 3 y 5 de
[../NOTES-raylang.md](../NOTES-raylang.md) son los que más cambiarían la forma del servidor.

## A/B de los escritores (controlado)

El resto de la tabla compara ejecuciones de versiones distintas y mide el RSS como marca de agua
acumulada del proceso, así que no aísla el escritor. `bench/writer_ab.ray` pone **los dos escritores
en el mismo binario** y `bench/writer_ab.py` mide cada uno en un proceso nuevo, restando el reposo:

```sh
ray build --native bench/writer_ab.ray -o /tmp/writer_ab
dd if=/dev/urandom of=/tmp/bench256.bin bs=1m count=256
python3 bench/writer_ab.py /tmp/writer_ab /tmp/bench256.bin 32 3
```

raylang 1.27.1 / net 0.3.1, 32 clientes, fichero de 256 MB, mediana de 3 rondas:

| Escritor | Reposo | Pico | **Delta** | **Por conexión** | Caudal |
|---|---|---|---|---|---|
| `direct` (bucle propio, un trozo, sin fibra) | 9,0 MB | 34,2 MB | **25,3 MB** | **808 KB** | 3.836 MB/s |
| `package` (`webserver.serve_file`, productor + canal) | 7,9 MB | 90,4 MB | **82,5 MB** | **2.642 KB** | 3.524 MB/s |

**3,3× de memoria por conexión y un 8% menos de caudal.** Los deltas del bucle propio son estables
(25/25/26 MB); los del paquete oscilan (90/83/73 MB) porque dependen de cuántos trozos hay en vuelo
en el instante de la muestra.

Extrapolado al límite de conexiones del servidor (128): ~100 MB frente a ~330 MB solo en buffers.
Con los 32 streams simultáneos que un servidor doméstico ve como mucho, la diferencia son 57 MB:
irrelevante. La decisión de raystream (usar `serve_file`) no cambia; lo que cambia es el número que
hay que citar.

## De dónde sale la memoria (descomposición)

`bench/fiber_cost.ray` mide por separado las tres piezas del patrón productor→canal→consumidor:

```sh
ray build --native bench/fiber_cost.ray -o /tmp/fiber_cost
/tmp/fiber_cost channel 32     # y: baseline 32 · fibers 32 · fibers 0 (runtime en vacío)
```

Muestrea el RSS desde fuera mientras el programa espera. Resultados en 1.27.1 (runtime vacío
6,5 MB): retener un trozo de 256 KB cuesta 267 KB, una fibra parada 54 KB, y el mismo trozo
cruzando un canal `bounded(1)` deja 490 KB — 1,8× la carga. Ver hallazgo 19 de NOTES-raylang.md.
