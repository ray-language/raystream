# Comprobador de hallazgos

Un repro mínimo por cada hallazgo de [../NOTES-raylang.md](../NOTES-raylang.md) y un programa que
los ejecuta todos contra la versión de raylang instalada:

```sh
ray run findings/check.ray                      # todo lo que se puede comprobar sin documentación
ray run findings/check.ray -- /ruta/llms.txt    # y además los de llms.txt (7 y 17)
```

`llms.txt` no se instala en disco; se puede sacar de la etiqueta de la versión:
`gh api "repos/ray-language/raylang/contents/llms.txt?ref=v1.27.16" --jq .content | base64 -d > /tmp/llms.txt`.

Estados: **OK** (resuelto y verificado) · **ABIERTO** (el repro sigue fallando) · **REGRESIÓN**
(estaba resuelto y ha vuelto) · **DECISIÓN** (fuera por decisión del proyecto raylang) · **MEDIR**
(hallazgo de rendimiento: lo cuantifica `bench/`) · **FUERA** (necesita algo que este proyecto no
usa, como el paquete `web`).

Tarda unos 15 s: compila a nativo cuatro veces (el proyecto entero y tres repros).
