# Banco de pruebas

Todo en raylang, como el servidor: el banco ejercita el lenguaje tanto como la aplicación (de hecho
portarlo desde Python destapó el hallazgo 21 y corrigió dos conclusiones — ver más abajo).

| Fichero | Qué hace |
|---|---|
| `harness.ray` | lo común: descargar descartando el cuerpo, lanzar N descargas en paralelo, muestrear el RSS de otro proceso con `ps`, medianas y formato |
| `throughput.ray` | caudal y concurrencia contra un servidor en marcha: descargas completas (1/4/16/32), `Range` pequeños (1/16/64 en paralelo), coste por petición y API JSON |
| `slow_clients.ray` | 24 clientes leyendo a ~80 KB/s: comprueba que la memoria no crece y que un cliente normal sigue atendido |
| `writer_ab.ray` | servidor con **los dos escritores** (bucle propio y `webserver.serve_file`) en el mismo binario |
| `writer_ab_run.ray` | arnés del A/B: proceso nuevo por medida, delta sobre el reposo, varias rondas y mediana |
| `fiber_cost.ray` | descompone el coste en memoria de una fibra, un trozo retenido y un trozo cruzando un canal |

El RSS se lee llamando a `ps` porque raylang no expone la memoria del proceso (hallazgo 20).

## Cómo se usa

```sh
# 1. Servidor (binario nativo: en la VM los números no son comparables)
ray build --native -o /tmp/raystream && /tmp/raystream --dir media --port 8080 &

# 2. Un fichero grande dentro de la biblioteca (no se versiona; bórralo al terminar)
dd if=/dev/urandom of=media/video/bench.mp4 bs=1m count=256

# 3. Su id, y a medir
ID=$(curl -s "localhost:8080/api/library?kind=video" \
     | python3 -c "import json,sys;print([x['id'] for x in json.load(sys.stdin) if x['name']=='bench.mp4'][0])")

ray build --native bench/throughput.ray   -o /tmp/bench_throughput && /tmp/bench_throughput   $ID 127.0.0.1:8080 raystream
ray build --native bench/slow_clients.ray -o /tmp/bench_slow       && /tmp/bench_slow         $ID 127.0.0.1:8080 raystream

rm media/video/bench.mp4
```

El A/B de escritores y la descomposición de memoria van por su cuenta:

```sh
dd if=/dev/urandom of=/tmp/bench256.bin bs=1m count=256
ray build --native bench/writer_ab.ray     -o /tmp/writer_ab
ray build --native bench/writer_ab_run.ray -o /tmp/bench_ab && /tmp/bench_ab /tmp/writer_ab /tmp/bench256.bin 32 3

ray build --native bench/fiber_cost.ray -o /tmp/fiber_cost
/tmp/fiber_cost channel 32     # y: baseline 32 · fibers 32 · fibers 0 (runtime en vacío)
```

Compila siempre a nativo: en la VM el mismo banco da números que no se pueden comparar con nada.

## Mediciones

Mac mini M4 (Mac16,10), loopback, fichero de 256 MB, binario nativo.

### Caudal y concurrencia (`throughput.ray`)

| Clientes | 1.27.1 / net 0.3.1 | **1.27.3 / net 0.3.2** |
|---|---|---|
| 1 | 5.333 MB/s · RSS 18,6 MB | 5.689 MB/s · RSS 16,8 MB |
| 4 | 4.472 MB/s · RSS 26,8 MB | 9.846 MB/s · RSS 18,5 MB |
| 16 | 4.108 MB/s · RSS 44,3 MB | 9.615 MB/s · RSS 20,8 MB |
| 32 | 3.899 MB/s · RSS 87,1 MB | **9.626 MB/s · RSS 22,0 MB** |

| Prueba | 1.27.1 / net 0.3.1 | 1.27.3 / net 0.3.2 |
|---|---|---|
| `Range` pequeño, secuencial | 0,14 ms · 7.142 req/s | 0,11 ms · 8.823 req/s |
| `Range` pequeño, 16 en paralelo | 11.130 req/s | 15.058 req/s |
| `Range` pequeño, 64 en paralelo | 14.222 req/s | 21.333 req/s |
| `/api/library` (pasa por el actor) | 0,16 ms · 6.122 req/s | 0,15 ms · 6.521 req/s |
| Miniatura PNG 480×480 (primera) | 26 ms nativo · 701 ms VM | 19 ms nativo · 700 ms VM |

Con 32 clientes: **2,5× de caudal y 4× menos memoria** que la versión anterior, sin tocar una línea
del servidor. El cambio es `FileBody` (M279 de `net` 0.3.2): el emisor lee el fichero en la propia
fibra de la conexión, sin fibra productora ni canal.

### A/B de escritores (`writer_ab_run.ray`, 32 clientes, mediana de 3 rondas)

| Escritor | Por conexión (net 0.3.1) | Caudal (0.3.1) | **Por conexión (0.3.2)** | **Caudal (0.3.2)** |
|---|---|---|---|---|
| `direct` (bucle propio, sin fibra) | 557 KB | 4.721 MB/s | **152 KB** | **8.410 MB/s** |
| `package` (`webserver.serve_file`) | 1.638 KB | 4.708 MB/s | **177 KB** | **8.623 MB/s** |

En 0.3.2 la diferencia se acaba: 177 KB contra 152 KB por conexión (1,16×) y el paquete va incluso
un pelo más rápido que el bucle escrito a mano. El rodeo propio ya no tendría sentido ni por
memoria ni por velocidad.

### Descomposición de memoria (`fiber_cost.ray`, runtime vacío 6,5 MB)

| Escenario | 1.27.1 | 1.27.3 |
|---|---|---|
| retener un trozo de 256 KB | 267 KB | 267 KB |
| una fibra viva parada | 54 KB | 52 KB |
| fibra + canal `bounded(1)` + un trozo cruzándolo | 490 KB | 486 KB |

Sin cambios: cruzar un canal sigue costando 1,8× la carga (hallazgo 19). Lo que cambió es que
`net` ya no pasa los ficheros por un canal.

Ver hallazgos 18, 19 y 20 en [../NOTES-raylang.md](../NOTES-raylang.md).

## Lo que cambió al portar el banco de Python a raylang

El banco original eran tres scripts de Python. Portarlos corrigió dos conclusiones, porque **el
cliente era parte del experimento**:

| | arnés en Python | arnés en raylang |
|---|---|---|
| `Range` pequeños, 64 en paralelo | 6.317 req/s | **14.222 req/s** |
| A/B: caudal `direct` vs `package` | 3.836 vs 3.524 MB/s (−8%) | 4.721 vs 4.708 MB/s (**−0,3%**) |
| A/B: memoria por conexión | 808 KB vs 2.642 KB (3,3×) | 557 KB vs 1.638 KB (**2,9×**) |

Es decir: **el 8% de caudal que le achacaba a `serve_file` era el cliente de Python**, no el
servidor; con un cliente que no es el cuello de botella, los dos escritores saturan lo mismo. La
diferencia de memoria sí es real, aunque algo menor de lo medido antes (un cliente más rápido vacía
los canales antes y deja menos trozos en vuelo).

Y el porte en sí encontró un fallo del lenguaje: `import std/sort;` impide compilar a nativo
(hallazgo 21).
