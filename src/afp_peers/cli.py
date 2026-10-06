"""CLI:  afp-peers download | peers | run   (ver README)."""
from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

from .config import Config
from .download import download_valor_fondo, download_valor_unidad
from .peers import compute_peers, coverage_report, to_index
from .soda import SodaClient

log = logging.getLogger("afp_peers")


def cmd_download(cfg: Config, full: bool) -> None:
    client = SodaClient(cfg.app_token, cfg.api_version, cfg.page_size)
    vf = download_valor_fondo(client, cfg, full=full)
    vu = download_valor_unidad(client, cfg, full=full)
    log.info("valor_fondo: %d filas (%s → %s)", len(vf), vf["fecha"].min(), vf["fecha"].max())
    log.info("valor_unidad: %d filas (%s → %s)", len(vu), vu["fecha"].min(), vu["fecha"].max())


def cmd_peers(cfg: Config) -> None:
    d = cfg.data_dir
    vf = pd.read_parquet(d / "valor_fondo.parquet")
    vu = pd.read_parquet(d / "valor_unidad.parquet")
    peers = compute_peers(vu, vf, cfg.afp_foco, cfg.lag_peso, cfg.excluir_patrimonios)
    idx = to_index(peers)
    cov = coverage_report(vu, vf)

    peers.to_parquet(d / "peers_diario.parquet", index=False)
    peers.to_csv(d / "peers_diario.csv", index=False)
    idx.to_csv(d / "peers_indices.csv", index=False)
    cov.to_csv(d / "cobertura.csv", index=False)
    log.info("Peers: %d filas, %d portafolios, hasta %s. Salidas en %s/",
             len(peers), peers["codigo_patrimonio"].nunique(), peers["fecha"].max(), d)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="afp-peers", description="Peers AFP ponderados por valor de fondo")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    dl = sub.add_parser("download", help="Descarga/actualiza valor de fondo y valor de unidad")
    dl.add_argument("--full", action="store_true", help="Re-descarga todo el histórico")
    sub.add_parser("peers", help="Calcula peers e industria con los datos locales")
    run = sub.add_parser("run", help="download + peers")
    run.add_argument("--full", action="store_true")
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    cfg = Config.load(a.config)
    if a.cmd in ("download", "run"):
        cmd_download(cfg, a.full)
    if a.cmd in ("peers", "run"):
        cmd_peers(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
