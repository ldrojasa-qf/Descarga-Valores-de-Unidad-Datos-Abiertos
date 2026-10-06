"""Descarga (completa o incremental) de valor de fondo y valor de unidad a Parquet."""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

import pandas as pd

from .config import Config
from .soda import SodaClient

log = logging.getLogger(__name__)

KEYS = ["fecha", "codigo_entidad", "codigo_patrimonio"]


def _clean_name(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace('"', "", regex=False).str.replace(r"\s+", " ", regex=True).str.strip()


def _start_date(path: Path, cfg: Config) -> str:
    """Fecha desde la que se descarga: histórico completo o últimos N días si ya existe el archivo."""
    if not path.exists():
        return cfg.fecha_inicio
    last = pd.read_parquet(path, columns=["fecha"])["fecha"].max()
    return (last.date() - timedelta(days=cfg.redescarga_dias)).isoformat()


def _merge_incremental(path: Path, new: pd.DataFrame, since: str) -> pd.DataFrame:
    if path.exists():
        old = pd.read_parquet(path)
        old = old[old["fecha"] < pd.Timestamp(since)]
        new = pd.concat([old, new], ignore_index=True)
    new = new.drop_duplicates(KEYS, keep="last").sort_values(KEYS).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    new.to_parquet(path, index=False)
    return new


# --------------------------------------------------------------------- fondo
def download_valor_fondo(client: SodaClient, cfg: Config, full: bool = False) -> pd.DataFrame:
    path = cfg.data_dir / "valor_fondo.parquet"
    if full and path.exists():
        path.unlink()
    since = _start_date(path, cfg)
    f = cfg.valor_fondo_filtro
    # codigo_columna y cod_renglon son numéricos en el dataset -> sin comillas
    where = (f"codigo_columna = {f['codigo_columna']} AND cod_renglon = {f['cod_renglon']} "
             f"AND fecha_corte >= '{since}T00:00:00'")
    select = ("fecha_corte, codigo_entidad, nombre_entidad, tipo_patrimonio, nombre_tipo_patrimonio, "
              "codigo_patrimonio, nombre_patrimonio, cod_unid_capt, sum_valor")
    log.info("Descargando valor de fondo desde %s", since)
    rows = client.fetch_all(cfg.datasets["valor_fondo"], select=select, where=where,
                            order="fecha_corte, codigo_entidad, codigo_patrimonio, cod_unid_capt")
    df = normalize_valor_fondo(pd.DataFrame(rows))
    return _merge_incremental(path, df, since)


def normalize_valor_fondo(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["fecha", "codigo_entidad", "nombre_entidad", "tipo_patrimonio", "nombre_tipo_patrimonio",
            "codigo_patrimonio", "nombre_patrimonio", "valor_fondo"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    df = df.rename(columns={"fecha_corte": "fecha", "sum_valor": "valor_fondo"})
    df["fecha"] = pd.to_datetime(df["fecha"]).dt.normalize()
    for c in ["codigo_entidad", "tipo_patrimonio", "codigo_patrimonio"]:
        df[c] = pd.to_numeric(df[c]).astype("int64")
    df["valor_fondo"] = pd.to_numeric(df["valor_fondo"], errors="coerce")
    for c in ["nombre_entidad", "nombre_tipo_patrimonio", "nombre_patrimonio"]:
        df[c] = _clean_name(df[c])
    dup = df.duplicated(KEYS, keep=False)
    if dup.any():
        log.warning("%d filas duplicadas (fecha, entidad, patrimonio) en valor de fondo; se conserva la última",
                    int(dup.sum()))
    return df.drop_duplicates(KEYS, keep="last")[cols]


# -------------------------------------------------------------------- unidad
def download_valor_unidad(client: SodaClient, cfg: Config, full: bool = False) -> pd.DataFrame:
    path = cfg.data_dir / "valor_unidad.parquet"
    if full and path.exists():
        path.unlink()
    since = _start_date(path, cfg)
    log.info("Descargando valor de unidad desde %s", since)
    rows = client.fetch_all(cfg.datasets["valor_unidad"],
                            select="fecha, codigo_entidad, nombre_entidad, codigo_patrimonio, nombre_fondo, valor_unidad",
                            where=f"fecha >= '{since}T00:00:00'",
                            order="fecha, codigo_entidad, codigo_patrimonio")
    df = normalize_valor_unidad(pd.DataFrame(rows))
    return _merge_incremental(path, df, since)


def normalize_valor_unidad(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["fecha", "codigo_entidad", "nombre_entidad", "codigo_patrimonio", "nombre_fondo", "valor_unidad"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["fecha"] = pd.to_datetime(df["fecha"]).dt.normalize()
    for c in ["codigo_entidad", "codigo_patrimonio"]:
        df[c] = pd.to_numeric(df[c]).astype("int64")
    df["valor_unidad"] = pd.to_numeric(df["valor_unidad"], errors="coerce")
    for c in ["nombre_entidad", "nombre_fondo"]:
        df[c] = _clean_name(df[c])
    return df.drop_duplicates(KEYS, keep="last")[cols]
