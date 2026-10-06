# afp-peers

Descarga los datos abiertos de la Superintendencia Financiera (datos.gov.co) de **Fondos de Pensiones Obligatorias y Cesantías** y calcula, para cada portafolio, el rendimiento diario de los **peers** de una AFP (por defecto Porvenir) y de la **industria**, ponderados por valor de fondo.

## Fuentes

| Dataset | ID | Uso |
|---|---|---|
| Valoración de los Tipos de Fondos de Pensiones Obligatorias y Cesantías C.P/L.P | [`hds9-4524`](https://dev.socrata.com/foundry/www.datos.gov.co/hds9-4524) | Valor del fondo al cierre en $ (ponderador) |
| Valor de unidad Fondos de Pensiones Obligatorias y Cesantías C.P/L.P | [`uawh-cjvi`](https://dev.socrata.com/foundry/www.datos.gov.co/uawh-cjvi) | Rendimiento diario |

**Filtro de valor de fondo.** `hds9-4524` trae ~1.9M filas con todos los renglones del formato (aportes, retiros, traslados, en unidades y en pesos). Solo se descarga, filtrado del lado del servidor:

- `codigo_columna = 2` → *VALOR EN PESOS $*
- `cod_renglon = 110` → *VALOR DEL PORTAFOLIO AL CIERRE DEL DÍA*, **después** de abonar rendimientos.

> El renglón 105 también se llama "valor al cierre", pero es antes de rendimientos: 110 = 105 + renglón 45 (rendimientos abonados en el día). Ej. Colfondos Cesantías LP, 01-ene-2015: 738.555.912.479,47 + 7.577.501,33 = 738.563.489.980,80.

Los `codigo_patrimonio` coinciden entre ambos datasets: `1` Cesantías LP, `2` Cesantías CP, `1000` Moderado, `5000` Conservador, `6000` Mayor Riesgo, `7000` Retiro Programado. `codigo_entidad`: 2 Protección, 3 Porvenir, 9 Skandia, 10 Colfondos.

## Metodología

Para cada portafolio *p* y fecha *t*:

```
r(i,t)          = VU(i,t) / VU(i,t-1) - 1
w(i,t)          = VF(i, t-1)                       # lag_peso = 1 (por defecto)
r_peers(p,t)    = Σ_{i ≠ Porvenir} w·r / Σ_{i ≠ Porvenir} w
r_industria(p,t)= Σ_{i}            w·r / Σ_{i}            w
```

- La ponderación es **por portafolio**, no por el AUM total de cada AFP.
- Se pondera con el valor de fondo de **t-1** (el AUM que generó el rendimiento de *t*). Con `lag_peso: 0` se usa el cierre de *t*, que ya incluye el rendimiento del día y sesga levemente el peso hacia quien rindió más.
- `hds9-4524` tiene huecos de fechas: si no hay valor de fondo exactamente en la fecha requerida se toma el último disponible hasta 7 días antes. Un peer sin peso en esa ventana sale del promedio ese día (ver `n_peers`).
- Los índices (`peers_indices.csv`) acumulan los rendimientos en base 100.

## SODA2 vs SODA3

| | SODA2 (`/resource/{id}.json`) | SODA3 (`/api/v3/views/{id}/query.json`) |
|---|---|---|
| Autenticación | Opcional (con token, más cuota) | **Obligatoria** (app token) |
| Método | GET con `$where`, `$limit`, `$offset` | POST con SoQL + `page` |
| Filtro en servidor | Sí | Sí |

**Por defecto se usa SODA2**: funciona sin credenciales (útil para clonar y correr) y permite el mismo filtro del lado del servidor, así que no se bajan las 1.9M filas sino solo los cierres en $. SODA3 queda disponible con `api.version: soda3` o `SODA_VERSION=soda3`. En ambos casos se recomienda un app token (gratuito, en *datos.gov.co → Developer Settings*) para evitar throttling.

## Uso

```bash
git clone <repo> && cd afp-peers
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

export SOCRATA_APP_TOKEN=xxxxxxxx    # opcional en SODA2, obligatorio en SODA3 (Windows: set ...)

afp-peers run            # descarga incremental + cálculo
afp-peers run --full     # re-descarga todo el histórico
afp-peers download       # solo descarga
afp-peers peers          # solo recalcula con los parquet locales
```

La primera corrida baja todo desde `fecha_inicio`; las siguientes solo re-descargan los últimos `redescarga_dias` (por defecto 45) para recoger correcciones retroactivas de la SFC.

## Salidas (`data/`)

| Archivo | Contenido |
|---|---|
| `valor_fondo.parquet` | Valor de fondo al cierre en $ por fecha, AFP y portafolio |
| `valor_unidad.parquet` | Valor de unidad por fecha, AFP y portafolio |
| `peers_diario.csv / .parquet` | `ret_foco`, `ret_peers`, `ret_industria`, `exceso_vs_peers`, `n_peers`, AUMs |
| `peers_indices.csv` | Índices base 100 de Porvenir, peers e industria por portafolio |
| `cobertura.csv` | Rango de fechas y # observaciones por serie en ambos datasets (control de calidad) |

Revisa `cobertura.csv` tras la primera descarga: series con `vf_obs` muy inferior a `vu_obs` indican días en que esa AFP quedará fuera del promedio.

## Configuración

`config.yaml`: AFP foco (`afp_foco`), rezago del peso (`lag_peso`), portafolios a excluir, fecha de inicio y versión de API.

## Tests

```bash
pytest
```

## Estructura

```
src/afp_peers/
  soda.py      cliente SODA2/SODA3 con paginación y reintentos
  download.py  descarga incremental y normalización a parquet
  peers.py     rendimientos, ponderación y agregación peers/industria
  cli.py       comandos download | peers | run
```
