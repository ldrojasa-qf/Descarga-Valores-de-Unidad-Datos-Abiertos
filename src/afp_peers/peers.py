"""Cálculo de rendimientos diarios de peers ponderados por valor de fondo.

Para cada portafolio p (Moderado, Conservador, Mayor Riesgo, Retiro Programado, Cesantías LP, CP):

    r_{i,t}      = VU_{i,t} / VU_{i,t-1} - 1                      (rendimiento diario por valor de unidad)
    w_{i,t}      = VF_{i,t-lag}                                    (valor del fondo al cierre, en $)
    r_peers_t    = Σ_{i ≠ foco} w_{i,t} r_{i,t} / Σ_{i ≠ foco} w_{i,t}
    r_industria_t= Σ_{i}        w_{i,t} r_{i,t} / Σ_{i}        w_{i,t}

Las ponderaciones son por portafolio (no por el total de la AFP): cada portafolio tiene su propia serie.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

PORTAFOLIOS = {
    1: "Cesantías Largo Plazo",
    2: "Cesantías Corto Plazo",
    1000: "Pensiones Moderado",
    5000: "Pensiones Conservador",
    6000: "Pensiones Mayor Riesgo",
    7000: "Pensiones Retiro Programado",
    8000: "Pensiones Alternativo",
}
SERIE = ["codigo_entidad", "codigo_patrimonio"]


def unit_returns(vu: pd.DataFrame) -> pd.DataFrame:
    """Rendimiento diario por serie (entidad, portafolio) entre fechas consecutivas reportadas."""
    df = vu.dropna(subset=["valor_unidad"]).sort_values(SERIE + ["fecha"]).copy()
    g = df.groupby(SERIE, sort=False)
    df["fecha_prev"] = g["fecha"].shift(1)
    df["ret"] = df["valor_unidad"] / g["valor_unidad"].shift(1) - 1
    return df.dropna(subset=["ret"])


def attach_weights(ret: pd.DataFrame, vf: pd.DataFrame, lag_peso: int = 1,
                   tolerancia_dias: int = 7) -> pd.DataFrame:
    """Pega el valor de fondo que pondera cada rendimiento.

    lag_peso=1 -> VF de la fecha del VU anterior (AUM que generó el rendimiento de t).
    lag_peso=0 -> VF de cierre de la misma fecha t.
    Si la fecha exacta no existe en valor de fondo se usa el último dato disponible
    hasta `tolerancia_dias` antes (la serie de valor de fondo tiene huecos).
    """
    key = "fecha_prev" if lag_peso else "fecha"
    left = ret.sort_values(key).copy()
    right = (vf[["fecha", *SERIE, "valor_fondo"]]
             .dropna(subset=["valor_fondo"])
             .rename(columns={"fecha": "fecha_vf"})
             .sort_values("fecha_vf"))
    out = pd.merge_asof(left, right, left_on=key, right_on="fecha_vf", by=SERIE,
                        direction="backward", tolerance=pd.Timedelta(days=tolerancia_dias))
    return out.rename(columns={"valor_fondo": "peso"})


def _wavg(d: pd.DataFrame, idx: list[str]) -> pd.DataFrame:
    d = d.dropna(subset=["ret", "peso"])
    d = d[d["peso"] > 0].assign(wr=lambda x: x["ret"] * x["peso"])
    g = d.groupby(idx).agg(wr=("wr", "sum"), aum=("peso", "sum"), n=("ret", "size"))
    g["ret"] = np.where(g["aum"] > 0, g["wr"] / g["aum"], np.nan)
    return g[["ret", "aum", "n"]]


def compute_peers(vu: pd.DataFrame, vf: pd.DataFrame, afp_foco: int = 3, lag_peso: int = 1,
                  excluir_patrimonios: list | None = None) -> pd.DataFrame:
    """Tabla larga: una fila por (fecha, portafolio) con foco, peers e industria."""
    r = attach_weights(unit_returns(vu), vf, lag_peso=lag_peso)
    if excluir_patrimonios:
        r = r[~r["codigo_patrimonio"].isin(excluir_patrimonios)]

    sin_peso = r["peso"].isna().mean()
    if sin_peso > 0:
        log.warning("%.1f%% de los rendimientos no tienen valor de fondo para ponderar "
                    "(se excluyen de peers/industria)", 100 * sin_peso)

    idx = ["fecha", "codigo_patrimonio"]
    foco = (r[r["codigo_entidad"] == afp_foco]
            .set_index(idx)[["ret", "peso"]]
            .rename(columns={"ret": "ret_foco", "peso": "aum_foco"}))
    peers = (_wavg(r[r["codigo_entidad"] != afp_foco], idx)
             .rename(columns={"ret": "ret_peers", "aum": "aum_peers", "n": "n_peers"}))
    ind = (_wavg(r, idx)
           .rename(columns={"ret": "ret_industria", "aum": "aum_industria", "n": "n_industria"}))

    out = peers.join(ind, how="outer").join(foco, how="left").reset_index()
    out["portafolio"] = out["codigo_patrimonio"].map(PORTAFOLIOS).fillna(out["codigo_patrimonio"].astype(str))
    out["exceso_vs_peers"] = out["ret_foco"] - out["ret_peers"]
    for c in ["n_peers", "n_industria"]:
        out[c] = out[c].fillna(0).astype(int)
    out = out.sort_values(["codigo_patrimonio", "fecha"]).reset_index(drop=True)
    return out[["fecha", "codigo_patrimonio", "portafolio", "ret_foco", "ret_peers", "ret_industria",
                "exceso_vs_peers", "n_peers", "n_industria", "aum_foco", "aum_peers", "aum_industria"]]


def to_index(peers: pd.DataFrame, base: float = 100.0) -> pd.DataFrame:
    """Índices acumulados base 100 por portafolio (equivalente a un 'valor de unidad' sintético)."""
    out = peers[["fecha", "codigo_patrimonio", "portafolio"]].copy()
    for c, name in [("ret_foco", "idx_foco"), ("ret_peers", "idx_peers"), ("ret_industria", "idx_industria")]:
        out[name] = (peers.assign(_r=peers[c].fillna(0.0))
                     .groupby("codigo_patrimonio")["_r"]
                     .transform(lambda s: base * (1 + s).cumprod()))
    return out


def coverage_report(vu: pd.DataFrame, vf: pd.DataFrame) -> pd.DataFrame:
    """Resumen por serie: rango de fechas en VU y VF, para detectar huecos o códigos que no cruzan."""
    a = vu.groupby(SERIE).agg(nombre=("nombre_entidad", "last"), vu_desde=("fecha", "min"),
                              vu_hasta=("fecha", "max"), vu_obs=("fecha", "size"))
    agg = dict(vf_desde=("fecha", "min"), vf_hasta=("fecha", "max"), vf_obs=("fecha", "size"))
    if "cod_renglon" in vf:
        agg["renglon_cierre"] = ("cod_renglon", lambda s: ",".join(map(str, sorted(s.unique()))))
    b = vf.groupby(SERIE).agg(**agg)
    out = a.join(b, how="outer").reset_index()
    out["portafolio"] = out["codigo_patrimonio"].map(PORTAFOLIOS)
    sin_vf = out[out["vf_obs"].isna()]
    for _, r in sin_vf.iterrows():
        log.warning("Sin valor de fondo: entidad %s, %s -> no entra en peers/industria",
                    r["codigo_entidad"], r["portafolio"] or r["codigo_patrimonio"])
    return out
