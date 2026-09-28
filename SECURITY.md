# Seguridad de raystream

raystream es un servidor de medios **personal**: sirve una carpeta de tu disco a tus propios
dispositivos. Este documento dice de qué se protege, cómo, y qué queda fuera.

## Modelo de amenazas

Tres fuentes de riesgo, en este orden de probabilidad:

1. **El contenido de la biblioteca no es de fiar.** Un MP3 descargado trae etiquetas ID3 escritas
   por otro, un fichero puede llamarse como quiera, un subtítulo o una imagen pueden pesar lo que
   quieran. Todo eso llega al servidor (que lo parsea) y a la interfaz (que lo muestra).
2. **Tu navegador visita otras webs.** Aunque el servidor sólo escuche en `127.0.0.1`, cualquier
   página abierta en tu navegador puede intentar hablar con ese puerto: leer tu catálogo, abrir una
   sala, incrustar tus medios.
3. **Otros equipos de la red**, cuando se arranca en modo red local para verlo desde la tele o el
   móvil.

Fuera de alcance: un atacante con acceso a tu cuenta o a tu disco, y exponer raystream a internet.

## Los dos modos

| | Loopback (por defecto) | Red local (`--host` que no sea loopback) |
|---|---|---|
| Quién llega al puerto | sólo esta máquina | cualquier equipo de la red |
| Control de acceso | se exige que `Host` sea un nombre del propio servidor y que un `Origin` presente coincida con él; con `--token`, además el token | **token obligatorio** en toda petición |
| Cómo se entra | `http://127.0.0.1:8080` | la URL con `?ray_token=…` que se imprime al arrancar, una vez por dispositivo |

En modo red local el token lo exige el propio paquete `net` antes de que la petición llegue a la
aplicación (`Limits.local_token`, un valor aleatorio de 128 bits; o el que se pase con `--token`,
de al menos 16 caracteres). Al entrar con la URL, el navegador guarda el token como cookie
`HttpOnly; SameSite=Strict`, así que la página, sus vídeos, el SSE y el WebSocket lo llevan sin
repetirlo.

```sh
raystream --dir ~/Música --host 0.0.0.0
# LAN mode: … Open this URL once on each device (it keeps a cookie):
#   http://<this machine's address>:8080/?ray_token=…
```

## Qué está protegido, y dónde

| Riesgo | Protección | Dónde |
|---|---|---|
| HTML en etiquetas o nombres de fichero que se ejecuta en la interfaz | la UI inserta todo dato de la biblioteca con `textContent` o como atributo; no hay `innerHTML` | `assets/app.js` |
| Ídem, como defensa en profundidad | `Content-Security-Policy` estricta: sólo scripts, estilos, imágenes, medios y conexiones del propio servidor; sin `unsafe-inline` | `src/security.ray` (`harden`) |
| Otra web que resuelve su dominio a 127.0.0.1 (DNS rebinding) para leer el catálogo | lista blanca de `Host` en modo loopback → `421` | `security.refuse` |
| Otra web que abre la sala, el SSE o la API desde tu navegador | un `Origin` que no coincide con el `Host` → `403`; en red local, además, el token | `security.refuse` |
| Otra web que incrusta tus medios o miniaturas | `Cross-Origin-Resource-Policy: same-origin` en toda respuesta | `harden` |
| Que el navegador interprete un fichero como otro tipo | `X-Content-Type-Options: nosniff`; el MIME sale del índice, no del contenido | `harden`, `serve_media` |
| El MIME de una carátula (viene del propio MP3) usado para colar cabeceras o servir un SVG | lista blanca de formatos rasterizados; cualquier otra cosa es `image/jpeg` | `probe.safe_image_mime` |
| Leer fuera de la biblioteca | toda ruta pasa por `scan.safe_path` (resuelve symlinks y comprueba que queda dentro); los assets, por el saneo del paquete; el escaneo ignora symlinks y ficheros ocultos | `catalog/scan.ray` |
| Ficheros enormes que se cargan enteros en memoria | subtítulos: tope de 4 MB comprobado con `stat` antes de leer; carátulas: se sirven como rango del propio fichero, sin leerlas; originales de imagen: en streaming; PNG: sólo se decodifican hasta 8 MB | `subtitles.ray`, `thumbs.ray`, `meta/probe.ray` |
| Conexiones de larga vida que ocupan todos los huecos | 32 suscriptores SSE y 32 miembros de sala como máximo (el servidor tiene 128 huecos); un SSE cerrado libera su hueco al momento | `live/events.ray`, `live/room.ray` |
| Salas: mensajes enormes, basura, amplificación | mensajes de 4 KB como máximo, 10 por segundo por miembro, campos validados (acción, id del catálogo, posición acotada), nombre de sala de 1–32 caracteres `[A-Za-z0-9_-]`, 16 salas como máximo, las vacías se borran | `live/room.ray`, `security.valid_*` |
| Filtrar datos del servidor | `/api/stats` da sólo el nombre de la carpeta, no la ruta; los errores no reflejan rutas ni ids | `main.ray` |
| Clickjacking, fugas por `Referer` | `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `Referrer-Policy: no-referrer` | `harden` |

Toda respuesta HTTP sale por un único punto (`emit` en `src/main.ray`) que aplica las cabeceras y,
en modo red local, siembra la cookie del token.

## Riesgos que quedan

- **Sin TLS.** En modo red local el tráfico —y con él el token— viaja en claro por la red. Úsalo
  sólo en redes de confianza, o pon delante un proxy con HTTPS.
- **En modo loopback no hay token por defecto.** Cualquier proceso de tu propia máquina puede leer
  la biblioteca. Para un equipo personal es aceptable; en una máquina compartida con otros
  usuarios, arranca con `--token <secreto>`: se exige también en loopback, y se entra una vez por
  la URL que se imprime.
- **Conexiones lentas.** Un cliente que abre conexiones y no envía la petición ocupa huecos hasta
  15 s (el plazo de lectura). En red local, alguien con el token podría mantenerlos ocupados.
- **Decodificar PNG cuesta memoria**: hasta 8 MB de entrada por miniatura no cacheada, multiplicado
  por las que se pidan a la vez. Tras la primera vez se sirve la caché de disco.
- **Etiquetas ID3v2.4 con desincronización**: la carátula se localiza como rango sin deshacerla; en
  esos ficheros (raros) la miniatura puede verse corrupta. No es un problema de seguridad.

## Cómo se verificó

- **Tests unitarios** de cada protección, en `ray test`: política de `Host` y `Origin`, modo red
  local con token, validadores de sala y de id, cabeceras de `harden`, topes de miembros, salas y
  suscriptores SSE, borrado de salas vacías, mensajes de sala inválidos que no se difunden, tope de
  subtítulos, lista blanca del MIME de carátulas, localización de la carátula como rango, y
  miniaturas que no cargan el original.
- **Comprobaciones funcionales** contra el servidor real: la aplicación entera sigue funcionando
  (UI, `Range`, `304`, subtítulos, miniaturas, SSE, salas), cada respuesta lleva las cabeceras, un
  `Host` ajeno recibe `421`, un `Origin` ajeno `403`, y el flujo del token en red local (sin token
  `403`; con él en la URL, `200` y cookie; después basta la cookie).
- Una batería local de pruebas de ataque contra una biblioteca hostil, que **no se publica** en este
  repositorio.

## Informar de un problema

Si encuentras una vulnerabilidad, no abras un issue público: usa el aviso privado de seguridad de
GitHub del repositorio (*Security → Report a vulnerability*).
