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
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from afp_peers.config import Config
from afp_peers.download import download_valor_fondo, download_valor_unidad
from afp_peers.peers import compute_peers, coverage_report, to_index
from afp_peers.soda import SodaClient

# Spyder deja handlers viejos entre corridas; force=True los reemplaza
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S", force=True)

# ---------------------------------------------------------------- parámetros
APP_TOKEN = ""          # pega aquí tu token de datos.gov.co (o déjalo vacío con SODA2)
FULL = False            # True = re-descarga todo el histórico
DESCARGAR = True        # False = solo recalcula con los parquet que ya tienes en data/
EXPORTAR_EXCEL = True   # guarda data/peers_afp.xlsx (requiere openpyxl)

if APP_TOKEN:
    os.environ["SOCRATA_APP_TOKEN"] = APP_TOKEN

cfg = Config.load(ROOT / "config.yaml")
cfg.data_dir = ROOT / cfg.data_dir
cfg.data_dir.mkdir(exist_ok=True)
# Para cambiar parámetros sin tocar config.yaml, p.ej.:
# cfg.lag_peso = 0
# cfg.afp_foco = 3
print(f"API: {cfg.api_version} | AFP foco: {cfg.afp_foco} | lag_peso: {cfg.lag_peso} | datos en: {cfg.data_dir}")

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

peers.to_parquet(cfg.data_dir / "peers_diario.parquet", index=False)
peers.to_csv(cfg.data_dir / "peers_diario.csv", index=False)
idx.to_csv(cfg.data_dir / "peers_indices.csv", index=False)
cov.to_csv(cfg.data_dir / "cobertura.csv", index=False)

# Último dato por portafolio
print(peers.groupby("portafolio").tail(1)[
    ["fecha", "portafolio", "ret_foco", "ret_peers", "ret_industria", "n_peers"]].to_string(index=False))

# %% 4. Rentabilidades acumuladas por ventana (foco vs peers vs industria)
def ventanas(peers: pd.DataFrame, dias=(30, 90, 180, 365, 365 * 3)) -> pd.DataFrame:
    out = []
    fin = peers["fecha"].max()
    for (cod, nom), g in peers.groupby(["codigo_patrimonio", "portafolio"]):
        for d in dias:
            w = g[g["fecha"] > fin - pd.Timedelta(days=d)]
            row = {"portafolio": nom, "ventana_dias": d}
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

port = idx["portafolio"].unique()
fig, axes = plt.subplots(len(port), 1, figsize=(10, 3 * len(port)), sharex=True)
axes = [axes] if len(port) == 1 else axes
for ax, p in zip(axes, port):
    d = idx[idx["portafolio"] == p].set_index("fecha")
    d[["idx_foco", "idx_peers", "idx_industria"]].plot(ax=ax, lw=1)
    ax.set_title(p)
    ax.legend(["Porvenir" if cfg.afp_foco == 3 else "Foco", "Peers", "Industria"], fontsize=8)
plt.tight_layout()
plt.show()

# %% 6. Exportar a Excel
if EXPORTAR_EXCEL:
    xlsx = cfg.data_dir / "peers_afp.xlsx"
    try:
        with pd.ExcelWriter(xlsx) as w:
            resumen.to_excel(w, sheet_name="resumen", index=False)
            peers.to_excel(w, sheet_name="peers_diario", index=False)
            idx.pivot_table(index="fecha", columns="portafolio",
                            values=["idx_foco", "idx_peers", "idx_industria"]).to_excel(w, sheet_name="indices")
            cov.to_excel(w, sheet_name="cobertura", index=False)
        print(f"Excel guardado en {xlsx}")
    except ModuleNotFoundError:
        print("Falta openpyxl: corre  %pip install openpyxl  en la consola y vuelve a ejecutar esta celda")
