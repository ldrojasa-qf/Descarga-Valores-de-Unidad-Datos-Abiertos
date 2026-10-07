# -*- coding: utf-8 -*-
"""
Ejecutar el pipeline de peers AFP desde Spyder.

- Corre todo con F5, o celda por celda con Ctrl+Enter (cada "# %%" es una celda).
- No requiere `pip install -e .`: agrega src/ al path automáticamente.
- Dependencias (una sola vez, en la consola IPython de Spyder):
      %pip install pandas pyarrow requests pyyaml
- Los DataFrames quedan en el Variable Explorer: vf, vu, peers, idx, cov.
"""

# %% 0. Setup
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)                      # para que config.yaml y data/ se resuelvan bien

# Spyder mantiene módulos importados entre corridas: si antes corriste otra copia del proyecto
# (p.ej. "afp-peers 2"), se seguiría usando ese código. Se limpia el cache y el path.
for _m in [m for m in sys.modules if m == "afp_peers" or m.startswith("afp_peers.")]:
    del sys.modules[_m]
sys.path[:] = [p for p in sys.path if not (Path(p).name == "src" and (Path(p) / "afp_peers").is_dir())]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from afp_peers.config import Config
from afp_peers.download import download_valor_fondo, download_valor_unidad
import yaml
from afp_peers.peers import (compute_peers, coverage_report, make_melt, make_wide_vf, make_wide_vu,
                             resolve_afp, to_index)
from afp_peers.soda import SodaClient
import afp_peers
print(f"Código cargado desde: {Path(afp_peers.__file__).parent}")

# Spyder deja handlers viejos entre corridas; force=True los reemplaza
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S", force=True)

# ---------------------------------------------------------------- parámetros
APP_TOKEN = ""          # pega aquí tu token de datos.gov.co (o déjalo vacío con SODA2)
FULL = False            # True = re-descarga todo el histórico
DESCARGAR = True        # False = solo recalcula con los parquet que ya tienes en data/
EXPORTAR_EXCEL = True   # guarda data/peers_afp.xlsx (requiere openpyxl)
AFP_FOCO = None         # quién eres: "Porvenir", "Proteccion", "Colfondos", "Skandia" (None = config.yaml)

# VU base de PEERS e INDUSTRIA para empalmar con serie histórica propia.
# Si está vacío ({}) se usa el VU de la AFP foco en la fecha ancla (comportamiento por defecto).
# Si se deja None, carga los valores de data/base_vu_<AFP>.yaml (los que ingresaste por CLI).
# Estos valores son de los peers de BASE_VU_AFP: con otra AFP foco no se usan.
# Ejemplo: BASE_VU = {"OBL MODERADO": {"PEERS": 45000.0, "INDUSTRIA": 44200.0}, ...}
BASE_VU =   BASE_VU = {
      "CES CP":          {"PEERS": 12597.941764152,  "INDUSTRIA": 12543.6095867919},
      "CES LP":          {"PEERS": 16884.7046868889,  "INDUSTRIA": 16798.4962988943},
      "OBL MAYOR RIESGO":{"PEERS": 13539.4296186389,  "INDUSTRIA": 13499.5542795162},
      "OBL MODERADO":    {"PEERS": 22961.8161757546,  "INDUSTRIA": 22829.7624690795},
      "OBL CONSERVADOR": {"PEERS": 13947.760617537,  "INDUSTRIA": 13886.9804668807},
      "OBL RET PROGRAMADO":{"PEERS": 13984.795686956,  "INDUSTRIA": 13987.1707221534},
  }

BASE_VU_AFP = "Porvenir"

if APP_TOKEN:
    os.environ["SOCRATA_APP_TOKEN"] = APP_TOKEN

cfg = Config.load(ROOT / "config.yaml")
cfg.data_dir = ROOT / cfg.data_dir
cfg.data_dir.mkdir(exist_ok=True)
# Para cambiar parámetros sin tocar config.yaml, p.ej.:
# cfg.lag_peso = 0
if AFP_FOCO is not None:
    cfg.afp_foco = resolve_afp(AFP_FOCO)
if BASE_VU and cfg.afp_foco != resolve_afp(BASE_VU_AFP):
    print(f"BASE_VU es de los peers de {BASE_VU_AFP}: no aplica a {cfg.afp_foco_nombre}, "
          f"se ancla al VU de {cfg.afp_foco_nombre}")
    BASE_VU = None
print(f"API: {cfg.api_version} | AFP foco: {cfg.afp_foco_nombre} ({cfg.afp_foco}) | lag_peso: {cfg.lag_peso} | datos en: {cfg.data_dir}")

# %% 1. Descarga (incremental; la primera vez baja todo desde fecha_inicio)
if DESCARGAR:
    client = SodaClient(cfg.app_token, cfg.api_version, cfg.page_size)
    vf = download_valor_fondo(client, cfg, full=FULL)
    vu = download_valor_unidad(client, cfg, full=FULL)
else:
    vf = pd.read_parquet(cfg.data_dir / "valor_fondo.parquet")
    vu = pd.read_parquet(cfg.data_dir / "valor_unidad.parquet")

print(f"valor_fondo : {len(vf):>8,} filas  {vf['fecha'].min():%Y-%m-%d} → {vf['fecha'].max():%Y-%m-%d}")
print(f"valor_unidad: {len(vu):>8,} filas  {vu['fecha'].min():%Y-%m-%d} → {vu['fecha'].max():%Y-%m-%d}")

# %% 2. Cobertura (revisar antes de confiar en los peers)
cov = coverage_report(vu, vf)
print(cov.to_string(index=False))

# %% 3. Peers e industria
peers = compute_peers(vu, vf, cfg.afp_foco, cfg.lag_peso, cfg.excluir_patrimonios)
idx = to_index(peers)
_bases_path = cfg.bases_path
_bases = BASE_VU if BASE_VU is not None else (
    yaml.safe_load(_bases_path.read_text(encoding="utf-8")) if _bases_path.exists() else None
)
vu_wide = make_wide_vu(vu, peers, cfg.afp_foco, bases=_bases)
vf_wide = make_wide_vf(vf, cfg.afp_foco)

peers.to_parquet(cfg.data_dir / "peers_diario.parquet", index=False)
peers.to_csv(cfg.data_dir / "peers_diario.csv", index=False)
idx.to_csv(cfg.data_dir / "peers_indices.csv", index=False)
cov.to_csv(cfg.data_dir / "cobertura.csv", index=False)
vu_wide.to_csv(cfg.data_dir / "vu_wide.csv", index=False)
make_melt(vu_wide).to_csv(cfg.data_dir / "vu_melt.csv", index=False)
vf_wide.to_csv(cfg.data_dir / "vf_wide.csv", index=False)
make_melt(vf_wide).to_csv(cfg.data_dir / "vf_melt.csv", index=False)

# Último dato por fondo
print(peers.groupby("fondo").tail(1)[
    ["fecha", "fondo", "ret_foco", "ret_peers", "ret_industria", "n_peers"]].to_string(index=False))

# %% 4. Rentabilidades acumuladas por ventana (foco vs peers vs industria)
def ventanas(peers: pd.DataFrame, dias=(30, 90, 180, 365, 365 * 3)) -> pd.DataFrame:
    out = []
    fin = peers["fecha"].max()
    for (cod, nom), g in peers.groupby(["codigo_patrimonio", "fondo"]):
        for d in dias:
            w = g[g["fecha"] > fin - pd.Timedelta(days=d)]
            row = {"fondo": nom, "ventana_dias": d}
            for c in ["ret_foco", "ret_peers", "ret_industria"]:
                row[c.replace("ret_", "acum_")] = (1 + w[c].fillna(0)).prod() - 1
            out.append(row)
    res = pd.DataFrame(out)
    res["exceso_vs_peers"] = res["acum_foco"] - res["acum_peers"]
    return res

resumen = ventanas(peers)
print(resumen.to_string(index=False, float_format=lambda x: f"{x:.4%}"))

# %% 5. Gráfica de índices (sale en el panel Plots de Spyder)
import matplotlib.pyplot as plt

port = idx["fondo"].unique()
fig, axes = plt.subplots(len(port), 1, figsize=(10, 3 * len(port)), sharex=True)
axes = [axes] if len(port) == 1 else axes
for ax, p in zip(axes, port):
    d = idx[idx["fondo"] == p].set_index("fecha")
    d[["idx_foco", "idx_peers", "idx_industria"]].plot(ax=ax, lw=1)
    ax.set_title(p)
    ax.legend([cfg.afp_foco_nombre, "Peers", "Industria"], fontsize=8)
plt.tight_layout()
plt.show()

# %% 6. Exportar a Excel
if EXPORTAR_EXCEL:
    xlsx = cfg.data_dir / "peers_afp.xlsx"
    try:
        with pd.ExcelWriter(xlsx) as w:
            resumen.to_excel(w, sheet_name="resumen", index=False)
            peers.to_excel(w, sheet_name="peers_diario", index=False)
            idx.pivot_table(index="fecha", columns="fondo",
                            values=["idx_foco", "idx_peers", "idx_industria"]).to_excel(w, sheet_name="indices")
            cov.to_excel(w, sheet_name="cobertura", index=False)
            vu_wide.to_excel(w, sheet_name="vu_wide", index=False)
            vf_wide.to_excel(w, sheet_name="vf_wide", index=False)
        print(f"Excel guardado en {xlsx}")
    except ModuleNotFoundError:
        print("Falta openpyxl: corre  %pip install openpyxl  en la consola y vuelve a ejecutar esta celda")
