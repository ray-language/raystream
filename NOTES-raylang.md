# Hallazgos de raylang — bitácora de raystream

Carencias, fricciones y mejoras encontradas mientras se construye **raystream** (servidor de medios,
ver [README.md](README.md)). Se anotan **durante** el trabajo, no al final.

Entorno original: raylang **1.26.0**, paquete Tier-2 **net 0.2.0** (aarch64-apple-darwin).
Las referencias `webserver.ray:N` son a `.ray-deps/net/webserver.ray` de esa versión.

Severidades: **bloqueante** (impide el caso de uso) · **fricción** (hay rodeo, cuesta tiempo) ·
**mejora** (funciona, pero podría ser mejor).

## Estado en raylang 1.27.4 / net 0.3.3 / web 0.4.2

Revisados uno a uno reejecutando cada repro. **Diecinueve de veintitrés resueltos.** Quedan
abiertos el 9 (JPEG), el 10 (bucles por píxel en la VM) y el 20 (el perfilador no mide memoria).
El 19 sigue vivo como coste del lenguaje, pero ya no afecta a este servidor: `net` evita el canal
por completo al servir ficheros (M279). El código de `net`/`web` cita los números de esta bitácora
(`M271 (raystream [3])`, `M272 (raystream [4])`, `M275 (raystream [18])`).

| # | Hallazgo | Estado |
|---|---|---|
| 1 | `pub fn get` rompía `std/json` | ✅ resuelto — el override es léxico a la raíz (M270); la stdlib conserva el suyo |
| 2 | `static_mount` leía el fichero entero | ✅ resuelto — camino perezoso (`fs.open`+`seek`+`read_bytes`) por encima de 1 MB |
| 3 | streaming sin `Content-Length` | ✅ resuelto — `stream_response_len(status, ch, length)` |
| 4 | `web` sin salida a streaming | ✅ resuelto — `r.stream`, `r.stream_len` y `r.sendfile` en web 0.4.0 |
| 5 | handler con estado no compilaba a nativo | ✅ resuelto — existe `serve_raw_with`, y el caso prohibido ahora es un error de **raylang**, no de rustc |
| 6 | `ray check` exigía `main` | ✅ resuelto — `ok: 'lib.ray' compiles (module without main)` |
| 7 | `llms.txt` sin tipos de retorno | ✅ resuelto — documenta `b[i]`, `m.get`, y una sección "Return types that surprise" |
| 8 | nombre de trait del prelude con posición inventada | ✅ resuelto — `1:1` de mi fichero, con extracto y mensaje corregido |
| 9 | `std/image` sólo decodifica PNG | ⬜ sigue igual |
| 10 | bucles por píxel lentos en la VM | ⬜ sigue igual (701 ms VM / 26 ms nativo) |
| 11 | `assert_eq` de enum derivado rompía el nativo | ✅ resuelto — compila, y `==` entre enums derivados ya funciona |
| 12 | `to_string` no aceptaba `Show` | ✅ resuelto |
| 13 | stubs que revientan en nativo | ✅ resuelto — el build del proyecto ya no emite el aviso |
| 14 | `ray fmt` recortaba los ceros del hex | ✅ resuelto — `0x0D, 0x0A` se conservan |
| 15 | `const` sin arrays | ✅ resuelto |
| 16 | `@derive(Show)` sin campos array | ✅ resuelto |
| 17 | `char_from_code` mal documentado | ✅ resuelto en 1.27.1 — `char_from_code(n) -> Option<char>` |
| 18 | el productor de `serve_file` bufferizaba 1 MB por conexión | ✅ resuelto del todo en net 0.3.2 — el emisor lee el fichero en la propia fibra de la conexión (`FileBody`, M279): 177 KB por conexión frente a 152 KB del bucle a mano |
| 19 | mover `bytes` por un canal cuesta 1,8× su tamaño | ⚠️ abierto en el lenguaje (486 KB medidos), pero `net` 0.3.2 ya no usa canal para servir ficheros |
| 20 | el perfilador no mide memoria ni sirve en servidores | ⬜ abierto |
| 21 | `import std/sort;` impedía compilar a nativo | ✅ resuelto en 1.27.3 |
| 22 | `ray fmt` corrompía interpolaciones anidadas con `//` | ✅ resuelto en 1.27.3 |
| 23 | `send_response` no sabía de HEAD: mandaba el cuerpo | ✅ resuelto en net 0.3.3 — `send_response_for(req, conn, r)` (M283) |

Los rodeos de raystream (bucle de accept propio, `serve_media` sobre la conexión cruda, comparar
`.show()` en los tests, `find_by_id` en vez de `get`) siguen siendo válidos, pero ya son **opcionales**:
se pueden revertir a la API del paquete cuando interese.

---

### [1] Un `pub fn` con nombre de builtin libre rompe la compilación de la stdlib — resolución de nombres · severidad: bloqueante

**Qué pasó:** al declarar `pub fn get(st: Store, id: string) -> Option<Item>` en
`src/catalog/store.ray`, el error no salió en mi módulo sino **dentro de `std/json`**:

```
[std/json] type error at 545:20: argument 1 of 'get': expected Store, got Map<string, std::json::Json>
  545 |         match (get(obj, k)) {
```

Mi función se coló en la resolución del `get` builtin que usa un módulo de la stdlib embebida.

**Repro mínimo** (un solo fichero, verificado con `ray_run`):

```raylang
import std/json;

pub struct Box { v: int, }

pub fn get(b: Box, key: string) -> int { b.v }

fn main() -> int {
    match (json.parse("{\"a\": 1}")) {
        Result.Ok(j) => print(json.stringify(j)),
        Result.Err(e) => print(e),
    }
    0
}
// → [std/json] type error at 545:20: argument 1 of 'get': expected Box, got Map<...>
```

Dos hechos distintos, los dos comprobados:

1. **La regla documentada está invertida.** `llms.txt` dice *"A builtin always beats a user function
   of the same name"*. No es así: gana la del usuario. Con `pub fn sort(b: Box) -> int` en el mismo
   fichero, `sort(xs)` sobre un `[int]` falla con `argument 1 of 'sort': expected Box, got [int]`.
2. **`pub` cruza a módulos ajenos.** Sólo afecta a los builtins **libres** (`get`, `sort`, …); los
   builtins de método (`len`, `push`…) se resuelven por receptor y conviven sin problema (probado
   con `pub fn len(b: Box)`: compila y ambos funcionan).

**Por qué importa:** el fallo aparece en un fichero que el programador no escribió ni puede tocar, con
un número de línea de la stdlib. Sin conocer la regla, es de los errores más desconcertantes posibles:
nada en el mensaje apunta a mi módulo. Y `get` es un nombre natural para cualquier repositorio, caché
o store — la colisión es casi inevitable en una aplicación real.

**Propuesta:** (a) que el builtin libre gane de verdad, como dice la documentación, o que la
declaración del usuario sea un error **en su propio fichero** (`'get' shadows a free builtin`) en vez
de romper la stdlib; (b) mientras tanto, listar en `llms.txt` el conjunto exacto de nombres libres
prohibidos como `pub` (hoy la lista está incompleta y el efecto descrito es el contrario al real).

**Rodeo aplicado:** renombrar a `find_by_id`.

---

### [2] `static_mount` carga el fichero entero en memoria para servir un `Range` — `net/webserver` · severidad: bloqueante (para medios)

**Qué pasó:** el camino de estáticos hace `fs.read_file_bytes(file)` del fichero completo
(webserver.ray:430) y sólo después recorta el rango con `data.sub_bytes(rng[0], rng[1] + 1)`
(webserver.ray:552). El comentario del propio módulo lo dice: *"la cola común de servir un estático
YA LEÍDO"*.

**Medido** (mismo fichero de 512 MB, mismo directorio, dos caminos — `spikes/static_mount_memory.ray`
frente a `src/http/serve_media.ray`):

| Camino | RSS tras un `Range: bytes=0-10` | RSS pico sirviendo el fichero entero |
|---|---|---|
| `webserver.static_mount` | **1 048 MB** | **1 561 MB** |
| `serve_media.serve_file` (este proyecto) | 31 MB | **34 MB** |

Once bytes pedidos cuestan un gigabyte de residente, y la memoria no se devuelve después.

**Por qué importa:** es exactamente la función que uno buscaría para montar una biblioteca de vídeo.
Un reproductor lanza una petición nueva en cada salto de la barra, así que el coste se paga una y
otra vez. El soporte de `Range` está impecablemente implementado (206, 416, `If-Range`, ETag) y aun
así es inservible para el caso de uso que más lo necesita.

**Propuesta:** un camino perezoso cuando hay `Range` o cuando el fichero supera un umbral:
`fs.open` + `fs.seek` + `fs.read_bytes` en bucle. O una función aparte, `static_stream_mount(prefix,
dir, req, conn)`, que escriba directamente en la conexión.

**Rodeo aplicado:** `src/http/serve_media.ray`, escrito sobre la conexión cruda. Memoria por
conexión: un chunk de 256 KB, sea cual sea el tamaño del fichero.

---

### [3] No se puede emitir un cuerpo en streaming con `Content-Length` — `net/webserver` · severidad: fricción

**Qué pasó:** `stream_response(status, ch)` es la única API pública de streaming y
`send_stream_response` fuerza siempre `Transfer-Encoding: chunked` + `Connection: close`
(webserver.ray:1169 y siguientes). No hay forma de emitir por trozos una respuesta cuyo tamaño se
conoce de antemano.

**Por qué importa:** un `206 Partial Content` necesita `Content-Length` exacto y `Content-Range`;
chunked no encaja ahí. Cualquier descarga o transferencia de tamaño conocido pierde además la barra
de progreso del cliente y el keep-alive.

**Propuesta:** `stream_response_sized(status: int, length: int, ch: Channel<bytes>) -> Response`
que emita `Content-Length` y mantenga la conexión viva.

---

### [4] El framework `web` no tiene salida a streaming — `packages/web` · severidad: fricción

**Qué pasó:** `Res` (framework.ray:58) sólo lleva `code`, `body`, `content_type`, `headers` y
`set_cookie`, y la conversión a la respuesta del webserver emite literalmente `stream: []`
(framework.ray:739). No hay acceso ni al canal ni a la conexión.

**Por qué importa:** el framework recomendado para escribir servidores es el que **no** puede hacer
SSE, descargas largas ni medios. En cuanto una aplicación real necesita una de esas tres cosas, hay
que abandonar el framework entero y bajar a `net/webserver`, perdiendo enrutado, middlewares y
sesiones. Es una frontera que se cruza en el peor momento: cuando la app ya está escrita.

**Propuesta:** `res.stream(ch)` (con su variante dimensionada del hallazgo 3) y/o
`res.sendfile(path)` que resuelva Range y ETag por dentro.

---

### [5] Un handler con estado para `serve_raw` no se puede compilar a binario nativo — `net/webserver` · severidad: bloqueante (nativo)

**Qué pasó:** `serve_raw` toma un `fn(Request, int)` pelado y no tiene variante builder —existen
`serve_with`, `serve_with_limits`, `serve_with_on`, `serve_with_tls` y `serve_with_graceful`
(webserver.ray:1734-1801), pero no `serve_raw_with`—, así que la única forma de darle estado es
capturarlo en un closure. Eso **funciona en la VM** y **no compila en nativo**:

```
error[E0277]: expected an `Fn(Rc<RefCell<net_CC_webserver_CC_Request>>, i64)` closure,
              found `Rc<{closure@src/main.rs:734:125: 734:205}>`
error[E0277]: `Rc<{closure@...}>` cannot be sent between threads safely
error[E0277]: `Rc<{closure@...}>` cannot be shared between threads safely
```

El backend nativo envuelve el closure en `Rc<…>`, que no implementa ni `Fn(...)` ni `Send + Sync`,
justo lo que la firma generada de `serve_raw` exige.

**Comprobado, las dos mitades:**

- `spikes/closure_handler.ray`: en la VM, el closure captura el canal de un actor y tres peticiones
  devuelven `hits=2`, `hits=3`, `hits=4`. El estado se comparte perfectamente entre fibras.
- `spawn` con capturas **sí** compila nativo (repro de 8 líneas: un `spawn(fn() { send(ch, 42); })`
  produce binario y imprime 42). El problema no son los closures con capturas, sino pasarlos como
  valor a una función genérica del paquete que exige `Send + Sync + Clone`.

**Por qué importa:** un servidor que necesite la conexión cruda (medios con `Range`, SSE, WebSocket
— es decir, todo lo que el hallazgo 4 empuja fuera del framework) no tiene ningún camino soportado
para llevar estado a sus handlers en un binario nativo. Y el fallo no aparece al escribir el
programa, sino al compilarlo para producción, en forma de errores de **Rust** sobre código generado.

**Propuesta:** `serve_raw_with(host, port, make_handler)`, simétrica con las demás; y que el
compilador rechace en raylang —con su mensaje y su línea— lo que el backend nativo no puede generar,
en vez de dejar que se filtren errores de rustc.

**Rodeo aplicado:** `src/http/listener.ray` — bucle de accept propio (`net.tcp_listen` +
`tcp_accept` + `spawn` por conexión + `webserver.read_request_limits`). El despachador se llama
DENTRO del `spawn`, nunca se pasa como valor. Compila nativo y, de paso, añade semáforo de
conexiones y timeout de petición.

---

### [6] `ray check` exige `main` en un módulo de librería — herramienta · severidad: fricción

**Qué pasó:** `ray_check` sobre `src/http/serve_media.ray` (un módulo sin `main`, importado por el
servidor) responde:

```
[serve_media] type error at 1:1: missing entry function 'main'
```

El módulo compila perfectamente: `ray_test` sobre el mismo fichero lo demuestra.

**Por qué importa:** `check` es la herramienta de bucle rápido por fichero, justo lo que quieres
mientras escribes un módulo que todavía no está cableado al programa. Obliga a pasar por `ray test`
(y por tanto a tener algún `@test`) o a compilar el proyecto entero.

**Propuesta:** que `check` sobre un fichero sin `main` type-checkee el módulo y calle, como hace
`test`. La exigencia de `main` tiene sentido en `run`/`build`, no en `check`.

---

### [7] La documentación de la stdlib no da los tipos de retorno — `llms.txt` · severidad: mejora

**Qué pasó:** el mapa de la stdlib lista `s.index_of(x)` entre los métodos de string sin decir que
devuelve `Option<int>`. Escribir lo natural cuesta un ciclo de compilación:

```
type error at 31:21: 'dash' is declared as int but initialized with Option<int>
```

Y hay omisiones que cuestan más que un ciclo de compilación:

- **`b[i]` no aparece**: el mapa de la stdlib lista `b.len() b.sub_bytes b.index_of b.starts_with`
  para `bytes`, pero no la indexación, que es la primitiva de todo parser binario. Al no verla,
  escribí un rodeo (`from_utf8(b.sub_bytes(i, i+1))` + `char_code`) que además es **incorrecto**
  para octetos ≥ 0x80. `REFERENCE.md` sí la documenta: `b[i] -> int` (0–255), en O(1).
- **`m.get(k)` tampoco**: `llms.txt` dice que leer un `Map` es "el `get` LIBRE", cuando existe el
  método `m.get(k)`. Usar el método desde el principio habría evitado por completo el hallazgo 1.

**Por qué importa:** el fichero existe precisamente para que un modelo escriba raylang correcto a la
primera. Los nombres sin firma obligan a adivinar o a consultar `ray_doc` uno por uno; con la firma
completa (`index_of(s, needle) -> Option<int>`) el error no ocurre. Y una primitiva ausente no se
traduce en un error de compilación sino en código peor: un rodeo que parece funcionar con datos
ASCII y falla con datos reales.

**Propuesta:** en el mapa de la stdlib de `llms.txt`, firma completa en los métodos cuyo retorno no
sea obvio — al menos los que devuelven `Option`/`Result`.

---

### [8] Un tipo con nombre de trait del prelude falla con una posición inventada — diagnósticos · severidad: fricción

**Qué pasó:** al declarar `struct Sub { ch: Channel<string>, reply: Channel<int> }` en
`src/live/events.ray`, el compilador respondió:

```
[catalog/scan] type error at 999993111:1: 'Sub' is already a type; it cannot also be a trait
```

Tres cosas mal en una sola línea: el módulo señalado (`catalog/scan`) no es el que declara `Sub`, la
posición `999993111:1` no existe, y no hay extracto de código ni cursor `^` — cuando `llms.txt`
promete que todo diagnóstico viene "always with source line and `^` cursor". El mensaje además
está invertido: lo que ya existía era el **trait** del prelude (`Sub`, el de la resta), y lo nuevo
es mi tipo.

**Repro mínimo** (8 líneas, verificado con `ray_run`):

```raylang
struct Sub { v: int, }

fn main() -> int {
    print(Sub { v: 1 }.v);
    0
}
// → type error at 1000000666:1: 'Sub' is already a type; it cannot also be a trait
```

Pasa igual con `Ord` (y presumiblemente con `Add`, `Mul`, `Eq`, `Show`, `Hash`, `Clone`…). El número
de línea cambia entre ejecuciones (`999993111`, `1000000666`, `1000000057`): parece un offset
sintético de un nodo del prelude, no una posición real.

**Por qué importa:** `Sub` es un nombre normalísimo (una suscripción, un subtítulo, una subconsulta).
El diagnóstico manda a mirar un fichero que no tiene nada que ver, y en un proyecto de veinte módulos
eso es una expedición.

**Propuesta:** apuntar a la declaración del usuario con su línea real y darle la vuelta al texto:
`'Sub' is a prelude trait; choose another name for this type`. Y, ya puestos, listar los nombres del
prelude reservados en `llms.txt` junto a los builtins libres del hallazgo 1.

**Rodeo aplicado:** renombrar el tipo a `Listener`.

---

### [9] `std/image` sólo decodifica PNG — `std/image` · severidad: mejora

**Qué pasó:** `std/image` expone exactamente `decode_png` y `encode_png`. Una biblioteca de fotos
real es mayoritariamente JPEG, y no hay forma de reescalar un JPEG sin escribir un decodificador
JPEG completo (Huffman + IDCT + submuestreo de croma) en raylang.

**Por qué importa:** en raystream, las miniaturas de PNG pesan 76 KB y las de JPEG pesan lo que pese
el original — puede ser un 5 MB por tarjeta de la galería. La diferencia entre una galería usable y
una que no lo es.

**Propuesta:** `decode_jpeg` (baseline al menos) en `std/image`, o una función de reescalado que
acepte los formatos que el runtime ya sabe leer. Con `encode_png` ya presente, con decodificar basta.

**Rodeo aplicado:** los PNG se reescalan de verdad; los demás formatos se sirven tal cual y la UI
los reduce por CSS.

---

### [10] Los bucles por píxel en la VM son caros — rendimiento · severidad: mejora

**Qué pasó:** reescalar un PNG con un filtro de caja (`src/thumbs.ray`) cuesta, para la MISMA
imagen de 480×480 servida por `/thumb`:

| Motor | Tiempo de la primera miniatura |
|---|---|
| VM (`ray run`) | **706 ms** |
| binario nativo (`ray build --native`) | **16 ms** |
| cacheada en disco | 0,4 ms |

43× de diferencia en aritmética entera sobre un `bytes`. En la VM son del orden de 1 µs por
píxel-operación.

**Por qué importa:** cualquier procesado de imagen o audio en tiempo real queda fuera de la VM. En
raystream se resuelve cacheando en disco (se paga una vez por fichero), pero una galería fría de 500
fotos serían diez minutos de espera.

**Propuesta:** documentar el orden de magnitud y la diferencia VM/nativo en `llms.txt` (para que uno
diseñe con caché desde el principio y sepa que el nativo no es un detalle de despliegue sino la
diferencia entre usable y no usable) y, si se puede, primitivas vectorizadas para `bytes` — un
`map_bytes` o un acceso por bloques que no pase por el intérprete en cada octeto.

---

### [11] `assert_eq` sobre un enum con `@derive(Eq)` impide compilar a nativo — backend nativo · severidad: bloqueante (nativo)

**Qué pasó:** los tests del enrutador comparaban rutas con `assert_eq(resolve("/"), Route.Home)`.
Pasan en la VM; el build nativo del proyecto entero falla con **nueve** errores de rustc:

```
error[E0369]: binary operation `==` cannot be applied to type `Rc<router_CC_Route>`
```

**Repro mínimo** (11 líneas; `ray run` imprime y pasa, `ray build --native` falla):

```raylang
@derive(Eq, Show)
enum Route { Home, Media(string), }

fn main() -> int {
    assert_eq(Route.Media("a"), Route.Media("a"));
    print("assert_eq on enums works");
    0
}
```

De paso, el `==` directo entre dos valores del mismo enum derivado tampoco se admite en la VM, y el
mensaje desorienta porque los tipos **sí** coinciden:

```
type error at 8:9: the operator '==' requires both operands of the same comparable type, not Route and Route
```

**Por qué importa:** un test verde en la VM deja el programa sin poder compilarse para producción, y
el error sale en Rust, sobre un `Rc<router_CC_Route>` que el programador nunca escribió. Comparar
valores de un enum es de las primeras cosas que uno hace al testear un enrutador o una máquina de
estados.

**Propuesta:** que el backend nativo genere `PartialEq` para los tipos con `@derive(Eq)` (y que `==`
funcione con ellos en los dos motores, que es lo que uno espera de un `derive(Eq)`).

**Rodeo aplicado:** comparar `.show()` en lugar de los valores.

---

### [12] `to_string` no acepta tipos con `Show`, aunque la referencia dice que sí — `std`/documentación · severidad: fricción

**Qué pasó:** `REFERENCE.md` §8 documenta el trait `Show` como `show(self) -> string` con la nota
*"enables `print`/`to_string`; derivable"*. Pero:

```
type error at 179:25: to_string only converts int/float/bool/string/char/bytes/u*, not router::Route
```

`print(valor)` sí funciona, y `valor.show()` también. El único que no cumple es `to_string`.

**Por qué importa:** es la forma natural de meter un valor en un mensaje de error o en una clave de
comparación, y la documentación la promete.

**Propuesta:** que `to_string` delegue en `Show` cuando el tipo lo implementa; o corregir la
referencia y decir explícitamente que ahí se usa `.show()`.

---

### [13] Funciones no soportadas en nativo se convierten en stubs que revientan — backend nativo · severidad: mejora

**Qué pasó:** el build nativo del proyecto avisa:

```
native build: warning: 1 function(s) not supported in the native subset — emitted as stubs that
panic if called at runtime:
  · std::time::monotonic_millis: builtin/function '__monotonic' is not supported in the native backend
```

Es decir: el binario se produce igualmente, y si el programa llega a esa rama, se cae en ejecución.
En raystream no se llama (el aviso viene de `std/time`, no de código propio) y el binario funciona,
pero la política es incómoda: una función de la stdlib que existe en la VM y no en nativo se
convierte en una mina en tiempo de ejecución.

**Propuesta:** que el aviso diga **quién** la llama (la cadena de llamadas desde el programa) para
poder juzgar si la mina es alcanzable, y que exista una opción para tratarlo como error.

---

### [14] `ray fmt` quita los ceros a la izquierda de los literales hexadecimales — herramienta · severidad: mejora

**Qué pasó:** al formatear el proyecto, la firma de un PNG pasó de

```raylang
bytes_of([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
```

a

```raylang
bytes_of([0x89, 0x50, 0x4E, 0x47, 0xD, 0xA, 0x1A, 0xA])
```

y `be32(0x00010000)` quedó en `be32(0x10000)`.

**Por qué importa:** en código de protocolos y formatos binarios —justo donde se escribe hex— el
ancho fijo **es** la información: una tabla de octetos alineada se lee de un vistazo y se compara
con la especificación; con anchos variables hay que contar. El formateador canónico está deshaciendo
una convención universal en ese dominio.

**Propuesta:** respetar el ancho escrito por el programador en los literales `0x` (o, como mínimo,
no recortar los de dos dígitos, que son octetos).

---

### [15] No hay constantes de tipo array — lenguaje · severidad: mejora

**Qué pasó:** declarar la lista de extensiones de subtítulo como constante del módulo no se admite:

```raylang
pub const EXTENSIONS: [string] = ["srt", "vtt"];
// → type error at 9:34: the value of constant 'EXTENSIONS' must be a literal
```

Como tampoco hay `var` de nivel superior, una tabla de consulta sólo puede vivir dentro de una
función, que la reconstruye en cada llamada. En `src/meta/id3.ray` la tabla de bitrates de MPEG se
crea entera cada vez que se mira un fichero.

**Por qué importa:** tablas de constantes (extensiones, MIMEs, bitrates, códigos de idioma) son pan
de cada día en código de formatos. Hoy se resuelven con cadenas de `if` o con arrays recreados en
caliente.

**Propuesta:** admitir literales compuestos (arrays y structs de literales) en `const`.

---

### [16] `@derive(Show)` no soporta campos de tipo array — anotaciones · severidad: mejora

**Qué pasó:** al añadir `tracks: [Track]` al struct `Item`, su `@derive(Show)` dejó de compilar:

```
type error at 41:1: cannot derive Show for a field of type [Track] (for now primitives, struct and enum)
```

**Por qué importa:** `Show` es lo que da `print` legible y lo que `assert_eq` usa para enseñar los
valores al fallar. Un struct de dominio con una lista dentro —que es la mitad de los structs de
dominio— se queda sin las dos cosas, y hay que quitar el derive del tipo entero.

**Propuesta:** derivar `Show` para `[T]` cuando `T: Show`; el mensaje ya sugiere que está previsto
("for now").

---

### [17] `llms.txt` documenta mal el tipo de `char_from_code` — `llms.txt` · severidad: fricción · **nuevo en 1.27.0**

**Qué pasó:** la nueva sección *"Return types that surprise"* —añadida justo para evitar el hallazgo
7— lista `char_from_code(n) -> char`. El tipo real sigue siendo `Option<char>`:

```
type error at 3:19: 'c' is declared as char but initialized with Option<char>
  3 |     let c: char = char_from_code(65);
```

**Por qué importa:** el error aparece en la única lista del fichero cuyo propósito es que no haya
que comprobar los tipos de retorno. Un dato equivocado ahí es peor que no tenerlo: invita a confiar.

**Propuesta:** `char_from_code(n) -> Option<char>` (`None` para un punto de código inválido, que es
lo que hace). Y una comprobación automática de esa tabla contra las firmas reales, que son las que
sirve `ray_doc`.

---

### [18] El productor de `serve_file` bufferiza 1 MB por conexión — `net/webserver` 0.3.0 · severidad: mejora · **nuevo**

**Qué pasó:** al cambiar el escritor propio de raystream por `webserver.serve_file` (el camino nuevo
del hallazgo 2), la memoria bajo carga se duplicó y el caudal bajó un ~11%. Mismo binario, misma
carga, mismo fichero de 256 MB:

| | escritor propio (un trozo de 256 KB) | `webserver.serve_file` |
|---|---|---|
| 16 clientes | 4.234 MB/s · RSS pico 64 MB | 3.783 MB/s · RSS pico 86 MB |
| 32 clientes | 4.107 MB/s · RSS pico 68 MB | 3.658 MB/s · RSS pico **144 MB** |

**Causa:** `stream_file_range` usa `Channel.bounded(4)` con trozos de 256 KB
(webserver.ray:587), así que cada transferencia puede tener **1 MB en vuelo** más la fibra
productora, frente a los 256 KB de un bucle directo. Con el `max_conns` por defecto (1024) el techo
teórico es de 1 GB sólo en buffers.

**Por qué importa:** no es un fallo —el backpressure funciona y 144 MB para 32 streams simultáneos
es asumible—, pero el tamaño del buffer es justo la decisión que un servidor de medios quiere
ajustar: con clientes reales (unos pocos MB/s) la cola de 4 no aporta nada y multiplica por cuatro
la memoria.

**Propuesta:** `bounded(1)` o `bounded(2)` por defecto, y un parámetro en `Limits` para el tamaño
del trozo y la profundidad de la cola de estáticos.

**Resuelto en net 0.3.1** (M275): la cola por defecto pasa a **1** y hay
`serve_file_with(file, req, chunk_bytes, queue)` y `static_mount_with` para ajustarlos. Medido
otra vez, mismo binario y misma carga:

| 32 clientes | caudal | RSS pico |
|---|---|---|
| escritor propio (256 KB, sin fibra) | 4.107 MB/s | 68 MB |
| `serve_file` de net 0.3.0 (cola 4) | 3.658 MB/s | 144 MB |
| **`serve_file` de net 0.3.1 (cola 1)** | **3.677 MB/s** | **86 MB** |
| `serve_file_with(1 MB, cola 1)` | 3.691 MB/s | 266 MB |

La memoria queda resuelta: 86 MB frente a los 68 MB del bucle directo son los dos trozos en vuelo
que el diseño necesita para solapar lectura y escritura.

**Lo que queda, y lo que NO es:** el caudal frente al bucle directo sigue por debajo, y **no es el
tamaño del trozo**: subirlo a 1 MB con `serve_file_with` triplicó el RSS y dejó el caudal igual. Es
el coste del salto productor→canal→escritor por trozo.

**Medición controlada** (`bench/writer_ab.ray`: los dos escritores en el MISMO binario, proceso
nuevo por medida, delta sobre el reposo, mediana de 3 rondas, 32 clientes). Las cifras anteriores
de esta nota comparaban picos absolutos de binarios distintos y con las fases del banco acumuladas
en el mismo proceso, que no es lo mismo:

| Escritor | Reposo | Pico | Delta | Por conexión | Caudal |
|---|---|---|---|---|---|
| bucle propio (un trozo, sin fibra) | 9,0 MB | 26,4 MB | 17,4 MB | **557 KB** | 4.721 MB/s |
| `webserver.serve_file` (productor + canal) | 12,6 MB | 63,9 MB | 51,1 MB | **1.638 KB** | 4.708 MB/s |

O sea: **2,9× de memoria por conexión, y el mismo caudal** (0,3% de diferencia, ruido).

**Corrección importante.** Las dos primeras versiones de esta nota decían "el doble de memoria" y
luego "3,3× y un 8% menos de caudal". Ambas se midieron con un arnés escrito en Python, y al
portarlo a raylang (`bench/writer_ab_run.ray`) el cliente dejó de ser el cuello de botella: **el 8%
de caudal era del cliente, no del servidor**, y la memoria por conexión baja a 2,9× porque un
cliente más rápido vacía los canales antes y deja menos trozos en vuelo. La diferencia de memoria
es real; la de velocidad no existía.

Con el límite de 128 conexiones son ~70 MB contra ~205 MB sólo en buffers, que sigue siendo una
diferencia de configuración. Si algún día importa, la vía sería que el emisor leyese el fichero él
mismo (un `sendfile`, o un modo sin fibra intermedia).

---

### [19] Mover `bytes` por un canal cuesta 1,8× su tamaño en residente — runtime/canales · severidad: mejora

**Qué pasó:** al descomponer los 2,6 MB por conexión del hallazgo 18 (`bench/fiber_cost.ray`, binario
nativo, 32 unidades, mediana de 3 rondas, sobre un runtime vacío de 6,5 MB):

| Escenario | RSS a n=32 | Por unidad |
|---|---|---|
| retener un trozo de 256 KB en la fibra principal | 14,9 MB | **267 KB** |
| una fibra viva parada, sin datos | 8,2 MB | **54 KB** |
| fibra + canal `bounded(1)` + **un** trozo de 256 KB cruzándolo | 21,8 MB | **490 KB** |

Retener el dato cuesta 267 KB (los 256 KB y poco más: la asignación es ajustada). Una fibra cuesta
54 KB. Pero **pasar ese mismo trozo por un canal deja 490 KB residentes**, 169 KB por encima de la
suma de sus partes, y el productor ya ha terminado cuando se mide: su copia está liberada, pero no
devuelta.

**Por qué importa:** el patrón productor→canal→consumidor es *el* patrón de streaming de raylang —
lo usan `serve_file`, `process.stream()` y cualquier tubería binaria. Pagar 1,8× el tamaño de la
carga en residente, y que no vuelva al sistema, es lo que convierte 808 KB por conexión en 2.642 KB
(hallazgo 18). Los canales copian en profundidad por diseño (heaps aislados), pero un `bytes` es
inmutable: ahí la copia no compra seguridad, sólo cuesta.

**Propuesta:** semántica de movimiento o recuento de referencias para los valores inmutables
(`bytes`, `string`) que cruzan un canal — el emisor cede su referencia en vez de copiar los
octetos. Y, si el asignador retiene por diseño, decirlo en la documentación: hoy un servidor parece
tener una fuga cuando lo que hay es memoria no devuelta.

**Aviso sobre la descomposición:** las tres piezas no suman los 2.642 KB por conexión del servidor
real (dos trozos en vuelo, la fibra de conexión, la respuesta, el buffer de escritura). Explican de
dónde viene el grueso, no el total exacto.

---

### [20] El perfilador no mide memoria, y no se puede usar en un servidor — `ray profile` · severidad: mejora

**Qué pasó:** buscando de dónde salen los 2,6 MB por conexión del hallazgo 18, `ray profile` no
puede responder. Su JSON completo es:

```json
{"wall_ns": …, "fibers": …, "functions": [{"name": …, "calls": …, "self_ns": …, "inclusive_ns": …}]}
```

Tiempo y llamadas. Ni octetos asignados, ni pico de heap, ni nada por función. Tres intentos:

1. **El perfilador**: sobre `bench/fiber_cost.ray channel 300` —300 trozos de 256 KB cruzando
   canales, 78 MB copiados— el informe es `main 13,1 ms` y `recv 1,3 ms`. La copia no aparece:
   ocurre dentro de los builtins y no tiene columna donde salir.
2. **`--heap N`** (tope de objetos vivos): por bisección, retener 32 trozos de 256 KB necesita
   **6 objetos**; el escenario del canal, 9. Un `bytes` es un objeto, pese lo que pese, así que el
   tope no ve la memoria de una tubería binaria.
3. **Sobre el servidor real**: `ray profile -- --port 8085` sirvió las ocho descargas bien, pero el
   informe se emite **al salir** y un servidor no sale. El proceso además **sobrevivió a `SIGTERM`**
   y a `SIGINT`, y no escribió el `--out`. Justo los programas que uno quiere perfilar —los que
   corren indefinidamente— son los que no puede.

**Por qué importa:** con un perfilador de tiempo se pueden resolver los problemas de CPU; para los
de memoria no hay nada dentro del lenguaje. Para llegar a "2,6 MB por conexión, 1,8× la carga al
cruzar un canal" hubo que salirse: muestrear el RSS con `ps` desde fuera y escribir micro-bancos a
mano (`bench/writer_ab.ray`, `bench/fiber_cost.ray`). Eso lo hace un especialista; el programador
medio simplemente no sabrá por qué su servidor ocupa lo que ocupa.

**Propuesta:**
1. Columnas de asignación por función: octetos asignados, liberados y pico vivo. Con eso, el
   hallazgo 19 se habría diagnosticado en un comando.
2. Emitir el informe con una señal (`SIGUSR1`, o `--profile-on-signal`), o volcarlo periódicamente:
   sin eso el perfilador no sirve para servidores ni para nada de larga vida.
3. Que `--heap` acepte también un tope en octetos, no sólo en objetos.
4. Y, aparte: revisar por qué un programa bajo `ray profile` ignora `SIGTERM`.

---

### [21] `import std/sort;` impide compilar a binario nativo — backend nativo / stdlib · severidad: bloqueante (nativo)

**Qué pasó:** al portar el banco de pruebas a raylang, el build nativo falló con tres errores de
**rustc** dentro de la stdlib:

```
error[E0277]: the trait bound `T: Ord` is not satisfied
    --> src/main.rs:3230:48
3230 | Rc::new(RefCell::new(__ray_sort(&a.clone()).borrow().iter().rev()…
note: required by a bound in `__ray_sort`
 101 | fn __ray_sort<T: Ord + Clone>(…)
```

Vienen de `std::sort::sort_desc` y `std::sort::dedup`: el código generado para esas dos funciones
genéricas pierde la cota `Ord`.

**Repro mínimo** (6 líneas; `ray run` imprime `[1, 2, 3]`, `ray build --native` falla):

```raylang
import std/sort;

fn main() -> int {
    let xs: [int] = [3, 1, 2];
    print(sort(xs));
    0
}
```

Quitando el `import` el mismo programa compila — `sort` es builtin libre. Es decir: **basta importar
el módulo**, sin llamar a nada suyo, para que el programa deje de compilarse a nativo.

**Por qué importa:** es el mismo patrón del hallazgo 5 —errores de rustc sobre código que el
programador no escribió— pero esta vez dentro de la propia stdlib, y sin ninguna pista de qué lo
provoca: el fichero del usuario no aparece en el mensaje.

**Propuesta:** añadir la cota `Ord` al código generado de `sort_desc`/`dedup` (o instanciar sólo lo
que se usa), y comprobar en CI que cada módulo de `std/` compila a nativo por sí solo.

**Rodeo aplicado:** no importar `std/sort` en `bench/harness.ray`.

---

### [22] `ray fmt` corrompe una interpolación anidada que contenga `//` — herramienta · severidad: bloqueante (corrompe el fuente)

**Qué pasó:** al formatear el banco de pruebas, `ray fmt -w` reescribió esta línea de
`bench/slow_clients.ray`

```raylang
print("latencia de /api/stats: ${elapsed_ms("http://${origin}/api/stats")} ms");
```

añadiéndole basura al final:

```raylang
print("latencia de /api/stats: ${elapsed_ms("http://${origin}/api/stats")} ms");  //${origin}/api/stats")} ms");
```

**Repro mínimo** (verificado):

```raylang
fn tag(s: string) -> string { s }

fn main() -> int {
    let origin: string = "127.0.0.1:8080";
    print("latencia: ${tag("http://${origin}/api")} ms");
    0
}
// ray fmt →  print("latencia: ${tag("http://${origin}/api")} ms");  //${origin}/api")} ms");
```

Con la misma forma pero sin `//` dentro (`"host/${origin}/api"`) el formateo es correcto: el
lexer del formateador trata el `//` de `http://` —dentro de una cadena que ya está dentro de una
interpolación— como principio de comentario, y duplica el resto de la línea.

**Y se acumula**: cada pasada de `ray fmt -w` añade otros 26 caracteres. Tres pasadas dejan la
línea en 136 caracteres, con tres colas repetidas.

**Por qué importa:** `fmt -w` reescribe el fichero. Es la única herramienta del juego cuyo fallo
**daña el código fuente**, y lo hace en silencio: el resultado sigue compilando (todo lo añadido es
comentario), así que ni el checker ni los tests lo detectan. Una URL dentro de un mensaje
interpolado no es un caso rebuscado.

**Propuesta:** que el lexer del formateador no busque comentarios dentro de una cadena, a cualquier
nivel de anidamiento. Y una prueba de idempotencia en CI: `fmt(fmt(x)) == fmt(x)` sobre el corpus,
que habría cazado esto solo.

**Rodeo aplicado:** sacar la URL a una variable antes de interpolarla.

---

### [23] `send_response` no puede responder un HEAD: manda el cuerpo entero — `net/webserver` · severidad: fricción · **nuevo**

**Qué pasó:** `serve_file` construye bien la respuesta de un HEAD —`Content-Length` real,
sin productor— pero el emisor público la escribe con cuerpo, porque `send_response(conn, r)` no
recibe la petición y no puede saber el método. Medido con un socket crudo contra raystream
dejando que el paquete resolviera el HEAD:

```
HEAD /media/<id> HTTP/1.1
→ Content-Length: 701316 · y 701.316 octetos de cuerpo detrás de las cabeceras
```

`curl -I` no lo delata: deja de leer al acabar las cabeceras. Sólo se ve leyendo el socket hasta el
cierre.

**Por qué importa:** en el camino crudo (`serve_raw*` + `send_response`), que es el que usa
cualquiera que necesite SSE, WebSocket o control del socket, **no hay forma de cumplir el RFC en un
HEAD**. El servidor `serve()` sí lo hace: su bucle interno conoce el método y pasa `omit_body`. La
capacidad existe, pero no está expuesta.

**Propuesta:** `send_response_for(req: Request, conn: int, r: Response) -> Result<int, string>`
—una línea, delega en el `omit_body` que ya existe— o un `send_response_head`.

**Resuelto en net 0.3.3** (M283): `send_response_for(req, conn, r)`, exactamente la propuesta. El
rodeo de `serve_media` desapareció y, de paso, **todas** las respuestas de raystream pasan ahora por
ese emisor, así que cumplen el RFC en un HEAD — incluidas las que antes ni lo intentaban.
Verificado a nivel de socket, cuerpo real de cada HEAD:

| Petición | Content-Length | Cuerpo |
|---|---|---|
| `HEAD /media/<id>` | 701316 | 0 octetos |
| `GET /media/<id>` | 701316 | 701316 octetos |
| `HEAD /` | 1000 | 0 |
| `HEAD /assets/app.js` | 6327 | 0 |
| `HEAD /subs/<id>/0` | 211 | 0 |
| `HEAD /api/stats` | 118 | 0 |
