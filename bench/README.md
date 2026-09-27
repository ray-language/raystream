# Banco de pruebas

Todo en raylang, como el servidor: el banco ejercita el lenguaje tanto como la aplicación (portarlo
desde Python destapó el hallazgo 21 y corrigió dos conclusiones — ver al final).

| Fichero | Qué hace |
|---|---|
| `harness.ray` | lo común: descargar descartando el cuerpo, N descargas en paralelo, muestrear el RSS de otro proceso con `ps`, medianas y formato |
| `throughput.ray` | caudal y concurrencia contra un servidor en marcha: descargas completas (1 a 128 clientes), `Range` pequeños, coste por petición y API JSON |
| `slow_clients.ray` | 24 clientes leyendo a ~80 KB/s: la memoria no crece y un cliente normal sigue atendido |
| `library_scale.ray` | cómo escala el indexado con el tamaño de la biblioteca (1.000 a 20.000 ficheros), etapa por etapa |
| `e2e_scale.ray` | el servidor real con una biblioteca grande: arranque, `/api/library`, medios y reindexado hasta el navegador |
| `probe_real.ray` | el probe con MP3 de tamaño real (varios MB, con carátula), en régimen |
| `synthetic.ray` · `gen_library.ray` | la biblioteca sintética con cabeceras reales que usan los dos anteriores |
| `fs_ops.ray` | el coste fijo de las operaciones de fichero de raylang |
| `writer_ab.ray` · `writer_ab_run.ray` | A/B de los dos escritores de fichero (bucle propio y `webserver.serve_file`) en el mismo binario |
| `fiber_cost.ray` | memoria de una fibra, de un trozo retenido y de un trozo cruzando un canal (hallazgo 19) |
| `pixel_loop.ray` | el coste de una miniatura, etapa por etapa, en VM y en nativo (hallazgo 10) |

El RSS se lee llamando a `ps` porque raylang no expone la memoria del proceso (hallazgo 20).

## Cómo se usa

Compila siempre a nativo: en la VM el mismo banco da números que no se pueden comparar con nada.

```sh
# Servidor, y un fichero grande dentro de la biblioteca (no se versiona; bórralo al terminar)
ray build --native -o /tmp/raystream && /tmp/raystream --dir media --port 8080 &
dd if=/dev/urandom of=media/video/bench.mp4 bs=1m count=256
ID=$(curl -s "localhost:8080/api/library?kind=video" \
     | python3 -c "import json,sys;print([x['id'] for x in json.load(sys.stdin) if x['name']=='bench.mp4'][0])")

ray build --native bench/throughput.ray   -o /tmp/b_throughput && /tmp/b_throughput $ID 127.0.0.1:8080 raystream
ray build --native bench/slow_clients.ray -o /tmp/b_slow       && /tmp/b_slow       $ID 127.0.0.1:8080 raystream
rm media/video/bench.mp4

# Escala de la biblioteca: el indexador por dentro, y el servidor por fuera
ray build --native bench/library_scale.ray -o /tmp/b_scale && /tmp/b_scale 1000 5000 20000
ray run bench/gen_library.ray -- /tmp/lib20k 20000
/tmp/raystream --dir /tmp/lib20k --port 8081 &
ray build --native bench/e2e_scale.ray -o /tmp/b_e2e && /tmp/b_e2e 127.0.0.1:8081 raystream /tmp/lib20k

# Probe con ficheros reales, coste de las operaciones de fichero
ray build --native bench/probe_real.ray -o /tmp/b_probe && /tmp/b_probe 300 4096 300
ray build --native bench/fs_ops.ray     -o /tmp/b_fsops && /tmp/b_fsops

# A/B de escritores y descomposición de memoria
dd if=/dev/urandom of=/tmp/bench256.bin bs=1m count=256
ray build --native bench/writer_ab.ray     -o /tmp/writer_ab
ray build --native bench/writer_ab_run.ray -o /tmp/b_ab && /tmp/b_ab /tmp/writer_ab /tmp/bench256.bin 32 3
ray build --native bench/fiber_cost.ray    -o /tmp/fiber_cost && /tmp/fiber_cost channel 32
```

## Mediciones · raylang 1.27.16 / net 0.4.1

Mac mini M4 (Mac16,10), loopback, binario nativo, 2026-09-27.

### Caudal y concurrencia (`throughput.ray`, fichero de 256 MB)

| Clientes simultáneos | Agregado | Por cliente | RSS pico |
|---|---|---|---|
| 1 | 6.244 MB/s | 6.244 MB/s | 16,6 MB |
| 4 | 10.449 MB/s | 2.612 MB/s | 18,5 MB |
| 16 | 10.751 MB/s | 672 MB/s | 20,8 MB |
| 32 | 10.266 MB/s | 321 MB/s | 21,3 MB |
| 64 | 10.176 MB/s | 159 MB/s | 24,2 MB |
| **128** (el límite del servidor) | **9.626 MB/s** | 75 MB/s | **30,2 MB** |

| Prueba | Resultado |
|---|---|
| `Range` pequeño, secuencial (conexión nueva cada vez) | 0,11 ms · 8.571 req/s |
| `Range` pequeño, 16 en paralelo | 16.000 req/s |
| `Range` pequeño, 64 en paralelo | 23.272 req/s |
| `/api/library` (biblioteca de muestra) | 0,06 ms · 15.789 req/s |
| 24 clientes lentos (`slow_clients.ray`) | RSS +3,3 MB · `/api/stats` en 1 ms · `Range` de 64 KB en 1 ms |

La concurrencia escala limpia hasta el límite: el agregado apenas cae y el reparto es equitativo;
con 128 descargas simultáneas el servidor ocupa 30 MB (~110 KB por conexión).

### Escala de la biblioteca (`library_scale.ray`, `e2e_scale.ray`)

Esta es la parte del análisis que cambió código de la aplicación. Con bibliotecas grandes aparecieron
tres cuellos de botella, los tres de raystream y no de raylang:

- **Emparejar subtítulos era cuadrático**: por cada `.srt` se recorría la biblioteca entera.
- **`/api/library` serializaba y concatenaba en cada petición**: el JSON se construía con
  `out = out + …` en un bucle (cuadrático) y además se volvía a serializar cada item cada vez.
- **Cada cambio en disco reindexaba y re-probeaba todo**, y cada petición de medios buscaba su item
  recorriendo la lista.

Indexador por dentro, 20.000 ficheros:

| Etapa | Antes | Después |
|---|---|---|
| Escaneo (recorrido + items + subtítulos) | 3.752 ms | **117 ms** |
| Indexado en frío (escaneo + probe) | 4.583 ms | **821 ms** |
| Reindexado tras añadir un fichero | 4.583 ms | **123 ms** |
| `/api/library` sin filtro | 4.317 ms | **1 ms** |

Crecimiento con N, ya lineal:

| N | recorrido | escaneo | frío | reindexado | publicar | `/api/library` | JSON |
|---|---|---|---|---|---|---|---|
| 1.000 | 3 ms | 7 ms | 39 ms | 6 ms | 8 ms | 0 ms | 248 KB |
| 5.000 | 11 ms | 27 ms | 192 ms | 29 ms | 39 ms | 0 ms | 1,2 MB |
| 20.000 | 43 ms | 117 ms | 821 ms | 123 ms | 159 ms | 1 ms | 4,9 MB |

`publicar` es el actor renderizando el JSON de cada item al recibir una versión nueva del índice: se
paga una vez por cambio, no por petición.

El servidor real de extremo a extremo, misma biblioteca de 20.000 ficheros:

| | Antes (commit `3e4a004`) | Después |
|---|---|---|
| Arranque (indexado) | 5.287 ms | **970 ms** |
| `/api/library` | 4.178 ms | **3 ms** |
| `/api/library` con filtro | 120 ms | **2 ms** |
| Fichero nuevo → aparece en el navegador | 5.850 ms | **579 ms** (400 son la ventana de coalescencia del vigilante) |
| 500 `Range` sobre `/media` | 7.352 req/s | 8.064 req/s |
| RSS en reposo | 40,6 MB | 49,1 MB |

El precio son +8,5 MB en reposo: el JSON prerenderizado de 20.000 items. Y no hay fuga: tras 600
respuestas de 5 MB (3 GB servidos) el RSS se queda plano en ~72 MB — es el asignador reteniendo, no
memoria perdida.

### El probe con ficheros reales (`probe_real.ray`, 300 MP3 de 4 MB)

| Carátula | Probe anterior | Probe actual |
|---|---|---|
| 300 KB | 66 µs por fichero | **23 µs** |
| 30 KB | 35 µs | **23 µs** |
| ninguna | — | 23 µs |

El anterior leía 256 KB de cada canción, releía la etiqueta si la carátula era mayor, y copiaba la
imagen a memoria para descartarla. El actual va de cabecera en cabecera de marco y **salta** la
carátula: lee ~10 KB por canción en vez de 256–566 KB. Con la caché caliente eso son 3× de CPU; con
la caché fría (el arranque tras reiniciar la máquina) la diferencia de I/O es de 25 a 50×.

Ojo al medir esto: la **primera pasada** justo después de generar los ficheros da ~270 µs por fichero
en los dos casos, porque coincide con el sistema volcando a disco lo recién escrito. `probe_real.ray`
hace tres pasadas y sólo las dos últimas son representativas.

### Operaciones de fichero (`fs_ops.ray`, caché caliente)

| Operación | Coste |
|---|---|
| `seek` + `read_bytes` de 10 octetos | 1,9 µs |
| `seek` solo | 0,25 µs |
| `open` + `close` | 7,2 µs |
| `read_bytes` de 256 KB | 5,5 µs |

Baratas: leer por trozos pequeños con `seek` no penaliza en raylang.

### Hallazgos de rendimiento del lenguaje

| | Resultado |
|---|---|
| A/B de escritores, 32 clientes: bucle propio | 174 KB por conexión · 9.799 MB/s |
| A/B de escritores, 32 clientes: `webserver.serve_file` | 187 KB por conexión · 9.592 MB/s |
| Fibra parada (`fiber_cost.ray`) | 42 KB (era 52–54 KB) |
| Trozo de 256 KB retenido | 266 KB |
| El mismo trozo cruzando un canal | 480 KB — 1,8× (hallazgo 19, sin cambios) |
| Miniatura PNG 480×480: VM (`pixel_loop.ray`) | 691 ms (`decode_png` 483 ms) |
| Miniatura PNG 480×480: nativo | 32 ms (hallazgo 10, en revisión) |

## Historia

### Evolución del caudal con cada versión (`throughput.ray`, 32 clientes)

| Versión | Agregado | RSS pico | Qué cambió |
|---|---|---|---|
| 1.27.1 / net 0.3.1 | 3.899 MB/s | 87,1 MB | productor + canal por conexión |
| 1.27.3 / net 0.3.2 | 9.626 MB/s | 22,0 MB | `FileBody`: el emisor lee el fichero (M279) |
| 1.27.4 / net 0.3.3 | 9.143 MB/s | 26,5 MB | `send_response_for` (M283) |
| **1.27.16 / net 0.4.1** | **10.266 MB/s** | **21,3 MB** | — |

### Lo que cambió al portar el banco de Python a raylang

El banco original eran tres scripts de Python. Portarlos corrigió dos conclusiones, porque **el
cliente era parte del experimento**:

| | arnés en Python | arnés en raylang |
|---|---|---|
| `Range` pequeños, 64 en paralelo | 6.317 req/s | **14.222 req/s** |
| A/B: caudal `direct` vs `package` | 3.836 vs 3.524 MB/s (−8%) | 4.721 vs 4.708 MB/s (**−0,3%**) |
| A/B: memoria por conexión | 808 KB vs 2.642 KB (3,3×) | 557 KB vs 1.638 KB (**2,9×**) |

El 8% de caudal que se le achacaba a `serve_file` era el cliente de Python. Y el porte encontró un
fallo del lenguaje: `import std/sort;` impedía compilar a nativo (hallazgo 21).
