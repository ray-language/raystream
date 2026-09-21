# raystream

Un servidor de medios personal escrito **enteramente en raylang**: indexa un directorio con vídeo,
audio e imágenes y los sirve por HTTP con seek real, biblioteca viva y reproducción sincronizada
entre varios navegadores. Sin ffmpeg ni ningún otro binario externo.

El proyecto es también un banco de pruebas del lenguaje: todo lo que roza o falta se anota en
[NOTES-raylang.md](NOTES-raylang.md).

## Arrancar

```sh
ray run                       # sirve ./media en http://127.0.0.1:8080
ray run -- --dir ~/Movies --port 9000
ray run -- --scan-only        # indexa, imprime el catálogo y sale
ray run -- --help
```

Si el directorio de la biblioteca está vacío, la aplicación genera contenido de muestra real
(PNG sintéticos y WAV con un tono) para que haya algo que ver desde el primer arranque. **Vídeo no
se genera**: sintetizar un fichero reproducible exigiría un codificador H.264, así que para probar
la parte de vídeo hay que copiar un vídeo de verdad dentro de la biblioteca — aparecerá solo, sin
reiniciar.

## Qué hace

- **Streaming con `Range`**: `200`, `206` con `Content-Range` exacto, `416`, `ETag`/`304` e
  `If-Range`, leyendo del disco por trozos — la memoria no depende del tamaño del fichero y el
  `<video>` del navegador salta por la barra sin recargar. Lo sirve `webserver.serve_file`; este
  proyecto pone el `Content-Type` del índice (el del paquete no conoce `.mov`, `.mkv`, `.m4a`,
  `.flac` ni `.ogg`) y responde los `HEAD`.
- **Catálogo con metadatos**, todos parseados en raylang: duración y dimensiones de MP4
  (`moov`/`mvhd`/`tkhd`), duración exacta de WAV (RIFF), etiquetas y carátula de MP3 (ID3v2.2/2.3/2.4)
  con estimación de duración, y dimensiones de PNG, JPEG, GIF y WebP sin decodificar la imagen.
- **Miniaturas**: los PNG se reescalan con un filtro de caja y se cachean en `.raystream/thumbs`;
  los audios con carátula embebida la sirven como miniatura.
- **Biblioteca viva**: un vigilante sobre eventos del kernel (`fs.watch`) reindexa al vuelo y empuja
  el cambio a los navegadores por Server-Sent Events. Copias un fichero en la biblioteca y aparece.
- **Salas sincronizadas**: varios clientes en la misma sala comparten qué se reproduce, dónde y si
  está en marcha, por WebSocket.
- **Subtítulos**: ficheros hermanos `.srt` y `.vtt` junto al medio (`demo.mov` + `demo.es.srt`), con
  el idioma tomado del nombre. Se sirven siempre como WebVTT —un `.srt` se convierte al vuelo— y la
  UI los engancha al reproductor como pistas seleccionables.
- **Búsqueda y filtros** por tipo, nombre, título, artista y álbum.

## Endpoints

| Ruta | Qué devuelve |
|---|---|
| `GET /` | la interfaz |
| `GET /assets/*` | CSS y JS (desde `std/embed`) |
| `GET /api/stats` | totales de la biblioteca |
| `GET /api/library?kind=&q=` | el catálogo filtrado |
| `GET /api/item/:id` | un item |
| `GET /media/:id` | el fichero, con `Range` |
| `GET /thumb/:id` | miniatura o carátula |
| `GET /subs/:id/:n` | la pista de subtítulos `n`, en WebVTT |
| `GET /events` | SSE: `hello`, `library_changed`, `ping` |
| `GET /ws/room/:name` | WebSocket de la sala |

## Arquitectura

Un solo puerto sobre `webserver.serve_raw_limits`, con el handler escrito **inline** en la llamada
—la forma que también compila a binario nativo— y una fibra por conexión. Se trabaja sobre la
conexión cruda porque es lo que permite el upgrade a WebSocket y abrir un stream SSE; los ficheros
los sirve `webserver.serve_file`, que desde `net` 0.3.0 hace ETag/304, `Range`/206/416 con
`If-Range` y, por encima de 1 MB, lee del disco por trozos con `Content-Length` exacto. El enrutado
y el catálogo son propios.

Cada conexión corre en su fibra. Como las fibras tienen heaps aislados, el estado compartido vive en
actores y se habla con ellos por canales:

```
                        ┌── fibra por conexión ──┐
  serve_raw_limits ────►│ router → handler       │
   (máx. 128 conex.)    └───┬───────┬────────┬───┘
                            │       │        │
                      catálogo    hub SSE   salas      ← un actor cada uno
                            ▲       ▲
                            └── vigilante fs.watch ──┘
```

| Módulo | Responsabilidad |
|---|---|
| `src/main.ray` | arranque, cableado y despacho de rutas |
| `src/config.ray` | argumentos y valores por defecto |
| `src/router.ray` | ruta → `Route` tipada, query string |
| `src/catalog/` | `scan` (árbol y rutas seguras), `library` (modelo y JSON), `indexer` (escaneo + metadatos), `store` (el actor) |
| `src/meta/` | `binary`, `id3`, `mp4`, `wav`, `imagemeta`, `probe` |
| `src/http/` | `serve_media` (MIME del índice, HEAD y log sobre `webserver.serve_file`), `api` (JSON) |
| `src/live/` | `events` (SSE + vigilante), `room` (salas WebSocket) |
| `src/subtitles.ray` | descubrimiento de pistas y conversión SRT → WebVTT |
| `src/thumbs.ray` | reescalado PNG y caché |
| `src/samples.ray` | contenido de muestra |
| `spikes/` | experimentos sueltos que respaldan los hallazgos |

## Binario nativo

```sh
ray build --native -o raystream        # binario autónomo, con los assets horneados
./raystream --dir ~/Movies --port 8080
```

Merece la pena para cualquier uso real: la misma miniatura (PNG de 480×480) tarda **706 ms** en la
VM y **16 ms** en el binario nativo, y 0,4 ms si ya está cacheada.

## Tests

```sh
ray test                      # la suite completa
ray test src/http/range.ray   # un módulo
```

Y las comprobaciones que no son unitarias:

```sh
# Range: 206 con el tramo pedido, y 416 cuando no es satisfacible
curl -sD- -o/dev/null -r 0-1023 "localhost:8080/media/<id>"
curl -sD- -o/dev/null -H 'Range: bytes=99999999-' "localhost:8080/media/<id>"

# Integridad: el fichero reensamblado por trozos es idéntico al original
curl -s -r 0-99999 "localhost:8080/media/<id>" >  p.bin
curl -s -r 100000- "localhost:8080/media/<id>" >> p.bin
shasum -a256 p.bin media/audio/tone-a440.wav

# Biblioteca viva
curl -sN localhost:8080/events &   # y copiar un fichero dentro de la biblioteca

# Salas
ray run spikes/room_client.ray     # dos clientes en una sala y uno en otra

# Subtítulos: un .srt sale convertido a WebVTT
curl -s "localhost:8080/subs/<id>/0" | head -4
```

## Límites conocidos

- Sin transcodificación ni HLS: el navegador tiene que saber reproducir el formato tal cual (es la
  consecuencia de no usar ffmpeg).
- Sin miniaturas de vídeo, por lo mismo.
- Las miniaturas de JPEG son el fichero original: `std/image` sólo decodifica PNG.
- Una petición por conexión (sin keep-alive), así que cada salto en la barra abre una conexión
  nueva. El límite es de 128 conexiones simultáneas, y cada conexión larga (SSE, sala) ocupa una.
- Cada transferencia mantiene ~1 MB en vuelo (el productor del paquete, hallazgo 18): 32 streams
  simultáneos son ~144 MB de RSS.
- La duración de un MP3 VBR es una estimación; la de MP4 y WAV es exacta.
- Los subtítulos son ficheros hermanos: no se extraen las pistas incrustadas en el contenedor
  (haría falta demuxar MP4/Matroska).

## Licencia

MIT — ver [LICENSE](LICENSE).
