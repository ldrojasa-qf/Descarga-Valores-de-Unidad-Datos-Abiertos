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
import unicodedata

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

FONDO_LABELS = {
    1: "CES LP",
    2: "CES CP",
    1000: "OBL MODERADO",
    5000: "OBL CONSERVADOR",
    6000: "OBL MAYOR RIESGO",
    7000: "OBL RET PROGRAMADO",
}
AFP_LABELS = {2: "PRO", 3: "PORVENIR", 9: "OLD", 10: "COL"}
AFP_ORDER = ["PRO", "PORVENIR", "COL", "OLD"]
AFP_NOMBRES = {2: "Protección", 3: "Porvenir", 9: "Skandia", 10: "Colfondos"}

SERIE = ["codigo_entidad", "codigo_patrimonio"]


def _norm(txt: str) -> str:
    return unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode().strip().upper()


def resolve_afp(afp: int | str) -> int:
    """codigo_entidad de la AFP foco a partir del código (3, "3") o del nombre ("Porvenir", "PRO", "colfondos")."""
    alias = {_norm(n): c for d in (AFP_LABELS, AFP_NOMBRES) for c, n in d.items()}
    txt = _norm(str(afp))
    cod = int(txt) if txt.isdigit() else alias.get(txt)
    if cod not in AFP_LABELS:
        validas = ", ".join(f"{c} = {n}" for c, n in AFP_NOMBRES.items())
        raise ValueError(f"AFP foco no reconocida: {afp!r}. Opciones: {validas}")
    return cod


def unit_returns(vu: pd.DataFrame) -> pd.DataFrame:
    """Rendimiento diario por serie (entidad, portafolio) entre fechas consecutivas reportadas."""
    df = vu.dropna(subset=["valor_unidad"]).sort_values(SERIE + ["fecha"]).copy()
    g = df.groupby(SERIE, sort=False)
    df["fecha_prev"] = g["fecha"].shift(1)
    df["ret"] = df["valor_unidad"] / g["valor_unidad"].shift(1) - 1
    return df.dropna(subset=["ret"])


def attach_weights(ret: pd.DataFrame, vf: pd.DataFrame, lag_peso: int = 0,
                   tolerancia_dias: int = 7) -> pd.DataFrame:
    """Pega el valor de fondo que pondera cada rendimiento.

    lag_peso=0 -> VF de cierre de la misma fecha t (por defecto).
    lag_peso=1 -> VF de la fecha del VU anterior (AUM que generó el rendimiento de t).
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


def compute_peers(vu: pd.DataFrame, vf: pd.DataFrame, afp_foco: int = 3, lag_peso: int = 0,
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
    out["fondo"] = out["codigo_patrimonio"].map(FONDO_LABELS).fillna(out["codigo_patrimonio"].astype(str))
    for c in ["n_peers", "n_industria"]:
        out[c] = out[c].fillna(0)

    # Corte estricto: si en una fecha falta cualquier AFP del grupo, ese grupo queda NaN
    # desde esa fecha en adelante. Se usa la última fecha con retorno disponible por AFP.
    _last = r.groupby(["codigo_patrimonio", "codigo_entidad"])["fecha"].max().reset_index()
    _cp = _last.query(f"codigo_entidad != {afp_foco}").groupby("codigo_patrimonio")["fecha"].min()
    _ci = _last.groupby("codigo_patrimonio")["fecha"].min()
    out = out.merge(_cp.rename("_cp"), on="codigo_patrimonio", how="left")
    out = out.merge(_ci.rename("_ci"), on="codigo_patrimonio", how="left")
    out.loc[out["fecha"] > out["_cp"], ["ret_peers", "aum_peers", "n_peers"]] = np.nan
    out.loc[out["fecha"] > out["_ci"], ["ret_industria", "aum_industria", "n_industria"]] = np.nan
    out = out.drop(columns=["_cp", "_ci"])

    out["exceso_vs_peers"] = out["ret_foco"] - out["ret_peers"]
    out = out.sort_values(["codigo_patrimonio", "fecha"]).reset_index(drop=True)
    return out[["fecha", "codigo_patrimonio", "fondo", "ret_foco", "ret_peers", "ret_industria",
                "exceso_vs_peers", "n_peers", "n_industria", "aum_foco", "aum_peers", "aum_industria"]]


def to_index(peers: pd.DataFrame, base: float = 100.0) -> pd.DataFrame:
    """Índices acumulados base 100 por portafolio (equivalente a un 'valor de unidad' sintético)."""
    out = peers[["fecha", "codigo_patrimonio", "fondo"]].copy()
    for c, name in [("ret_foco", "idx_foco"), ("ret_peers", "idx_peers"), ("ret_industria", "idx_industria")]:
        out[name] = (peers.assign(_r=peers[c].fillna(0.0))
                     .groupby("codigo_patrimonio")["_r"]
                     .transform(lambda s: base * (1 + s).cumprod()))
    return out


# ---------------------------------------------------------------- tablas anchas y melt
def make_wide_vu(vu: pd.DataFrame, peers_df: pd.DataFrame, afp_foco: int = 3,
                 bases: dict | None = None) -> pd.DataFrame:
    """Wide VU: (fecha, fondo) × (PRO, PORVENIR, COL, OLD, PEERS, INDUSTRIA).

    PEERS e INDUSTRIA son VU sintéticos. El valor base (ancla) por defecto es el VU del
    foco en la fecha previa al primer rendimiento. Si se pasa `bases`, un dict con la forma
    {"OBL MODERADO": {"PEERS": 45000.0, "INDUSTRIA": 44500.0}, ...}, se usa ese valor
    como ancla (útil para empalmar con una serie histórica propia).
    """
    vu2 = (vu[vu["codigo_patrimonio"].isin(FONDO_LABELS) & vu["codigo_entidad"].isin(AFP_LABELS)]
           .copy())
    vu2["fondo"] = vu2["codigo_patrimonio"].map(FONDO_LABELS)
    vu2["adm"] = vu2["codigo_entidad"].map(AFP_LABELS)

    wide = (vu2
            .pivot_table(index=["fecha", "fondo"], columns="adm", values="valor_unidad", aggfunc="last")
            .reset_index()
            .rename_axis(columns=None))
    for c in AFP_ORDER:
        if c not in wide.columns:
            wide[c] = np.nan

    foco_label = AFP_LABELS[afp_foco]
    foco_vu_all = (vu[(vu["codigo_entidad"] == afp_foco) & vu["codigo_patrimonio"].isin(FONDO_LABELS)]
                   .sort_values("fecha"))

    synth_parts = []
    for pat, fondo_label in FONDO_LABELS.items():
        p_ret = (peers_df[peers_df["codigo_patrimonio"] == pat]
                 .sort_values("fecha")[["fecha", "ret_peers", "ret_industria"]])
        if p_ret.empty:
            continue

        t1 = p_ret["fecha"].iloc[0]
        foco_vu = (foco_vu_all[foco_vu_all["codigo_patrimonio"] == pat]
                   .set_index("fecha")["valor_unidad"])
        before_t1 = foco_vu[foco_vu.index < t1]
        auto_base = before_t1.iloc[-1] if len(before_t1) else (foco_vu.iloc[0] if len(foco_vu) else 100.0)

        base_p = bases[fondo_label]["PEERS"] if (bases and fondo_label in bases) else auto_base
        base_i = bases[fondo_label]["INDUSTRIA"] if (bases and fondo_label in bases) else auto_base

        p_ret = p_ret.set_index("fecha")
        cutoff_p = p_ret["ret_peers"].dropna().index.max() if p_ret["ret_peers"].notna().any() else None
        cutoff_i = p_ret["ret_industria"].dropna().index.max() if p_ret["ret_industria"].notna().any() else None
        syn_p = base_p * (1 + p_ret["ret_peers"].fillna(0.0)).cumprod()
        syn_i = base_i * (1 + p_ret["ret_industria"].fillna(0.0)).cumprod()
        if cutoff_p is not None:
            syn_p[syn_p.index > cutoff_p] = np.nan
        if cutoff_i is not None:
            syn_i[syn_i.index > cutoff_i] = np.nan
        synth_parts.append(pd.DataFrame({"fecha": syn_p.index, "fondo": fondo_label,
                                         "PEERS": syn_p.values, "INDUSTRIA": syn_i.values}))

    if synth_parts:
        wide = wide.merge(pd.concat(synth_parts, ignore_index=True), on=["fecha", "fondo"], how="left")
    else:
        wide["PEERS"] = np.nan
        wide["INDUSTRIA"] = np.nan

    return (wide[["fecha", "fondo"] + AFP_ORDER + ["PEERS", "INDUSTRIA"]]
            .sort_values(["fondo", "fecha"])
            .reset_index(drop=True))


def make_wide_vf(vf: pd.DataFrame, afp_foco: int = 3) -> pd.DataFrame:
    """Wide VF: (fecha, fondo) × (PRO, PORVENIR, COL, OLD, PEERS, INDUSTRIA).

    PEERS = suma del VF de todas las entidades distintas al foco.
    INDUSTRIA = suma del VF de todas las entidades.
    """
    vf2 = (vf[vf["codigo_patrimonio"].isin(FONDO_LABELS) & vf["codigo_entidad"].isin(AFP_LABELS)]
           .copy())
    vf2["fondo"] = vf2["codigo_patrimonio"].map(FONDO_LABELS)
    vf2["adm"] = vf2["codigo_entidad"].map(AFP_LABELS)

    wide = (vf2
            .pivot_table(index=["fecha", "fondo"], columns="adm", values="valor_fondo", aggfunc="last")
            .reset_index()
            .rename_axis(columns=None))
    for c in AFP_ORDER:
        if c not in wide.columns:
            wide[c] = np.nan

    foco_label = AFP_LABELS[afp_foco]
    peers_cols = [c for c in AFP_ORDER if c != foco_label]
    wide["PEERS"] = np.where(wide[peers_cols].notna().all(axis=1), wide[peers_cols].sum(axis=1), np.nan)
    wide["INDUSTRIA"] = np.where(wide[AFP_ORDER].notna().all(axis=1), wide[AFP_ORDER].sum(axis=1), np.nan)

    return (wide[["fecha", "fondo"] + AFP_ORDER + ["PEERS", "INDUSTRIA"]]
            .sort_values(["fondo", "fecha"])
            .reset_index(drop=True))


def make_melt(wide: pd.DataFrame) -> pd.DataFrame:
    """Melt de la tabla ancha: FECHA, FONDO, ADMINISTRADORA, VALOR, VALOR_LAG.

    VALOR_LAG es el valor de la misma (FONDO, ADMINISTRADORA) en la fecha anterior disponible.
    """
    adm_cols = [c for c in wide.columns if c not in ("fecha", "fondo")]
    long = (wide
            .melt(id_vars=["fecha", "fondo"], value_vars=adm_cols,
                  var_name="ADMINISTRADORA", value_name="VALOR")
            .rename(columns={"fecha": "FECHA", "fondo": "FONDO"})
            .sort_values(["FONDO", "ADMINISTRADORA", "FECHA"])
            .reset_index(drop=True))
    long["VALOR_LAG"] = long.groupby(["FONDO", "ADMINISTRADORA"])["VALOR"].shift(1)
    return long


def coverage_report(vu: pd.DataFrame, vf: pd.DataFrame) -> pd.DataFrame:
    """Resumen por serie: rango de fechas en VU y VF, para detectar huecos o códigos que no cruzan."""
    a = vu.groupby(SERIE).agg(nombre=("nombre_entidad", "last"), vu_desde=("fecha", "min"),
                              vu_hasta=("fecha", "max"), vu_obs=("fecha", "size"))
    agg = dict(vf_desde=("fecha", "min"), vf_hasta=("fecha", "max"), vf_obs=("fecha", "size"))
    if "cod_renglon" in vf:
        agg["renglon_cierre"] = ("cod_renglon", lambda s: ",".join(map(str, sorted(s.unique()))))
    b = vf.groupby(SERIE).agg(**agg)
    out = a.join(b, how="outer").reset_index()
    out["fondo"] = out["codigo_patrimonio"].map(FONDO_LABELS)
    sin_vf = out[out["vf_obs"].isna()]
    for _, r in sin_vf.iterrows():
        log.warning("Sin valor de fondo: entidad %s, %s -> no entra en peers/industria",
                    r["codigo_entidad"], r["fondo"] or r["codigo_patrimonio"])
    return out
