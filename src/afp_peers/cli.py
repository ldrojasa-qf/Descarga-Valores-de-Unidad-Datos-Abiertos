"""CLI:  afp-peers download | peers | run   (ver README)."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_IS_TTY = sys.stdin.isatty()

import pandas as pd
import yaml

from .config import Config
from .download import check_vf_coverage, download_valor_fondo, download_valor_unidad
from .peers import (FONDO_LABELS, compute_peers, coverage_report, make_melt,
                    make_wide_vf, make_wide_vu, resolve_afp, to_index)
from .soda import SodaClient

log = logging.getLogger("afp_peers")


def _load_bases(path: Path) -> dict:
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def _save_bases(path: Path, bases: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(bases, allow_unicode=True, default_flow_style=False), encoding="utf-8")


def _prompt_bases(vu: pd.DataFrame, peers_df: pd.DataFrame, afp_foco: int,
                  bases_path: Path) -> dict:
    """Pregunta por consola el valor de unidad base de PEERS e INDUSTRIA por fondo.

    Muestra como referencia el VU del foco y la fecha ancla. Carga/guarda en bases_path.
    Los valores guardados aparecen como default (Enter = mantener).
    """
    from .peers import AFP_LABELS, make_wide_vu as _mwu  # import local para no contaminar

    saved = _load_bases(bases_path)

    # Calcular la fecha ancla y el VU del foco por fondo (misma lógica que make_wide_vu)
    foco_label = AFP_LABELS.get(afp_foco, str(afp_foco))
    foco_vu_all = vu[(vu["codigo_entidad"] == afp_foco) & vu["codigo_patrimonio"].isin(FONDO_LABELS)]

    print("\n" + "=" * 60)
    print("CONFIGURACIÓN DE VU BASE PARA PEERS E INDUSTRIA")
    print("  Sirve para empalmar con tu serie histórica propia.")
    print(f"  Enter = mantener el valor guardado (o usar VU de {foco_label}).")
    print("=" * 60)

    bases: dict = {}
    for pat, fondo_label in FONDO_LABELS.items():
        p_ret = peers_df[peers_df["codigo_patrimonio"] == pat].sort_values("fecha")
        if p_ret.empty:
            continue

        t1 = p_ret["fecha"].iloc[0]
        foco_vu = foco_vu_all[foco_vu_all["codigo_patrimonio"] == pat].set_index("fecha")["valor_unidad"]
        before_t1 = foco_vu[foco_vu.index < t1]
        auto_base = before_t1.iloc[-1] if len(before_t1) else (foco_vu.iloc[0] if len(foco_vu) else 100.0)
        anchor_date = before_t1.index[-1] if len(before_t1) else (foco_vu.index[0] if len(foco_vu) else t1)

        print(f"\n  {fondo_label}  (fecha ancla: {anchor_date:%Y-%m-%d} | VU {foco_label}: {auto_base:,.4f})")

        saved_fondo = saved.get(fondo_label, {})
        bases[fondo_label] = {}

        for serie in ("PEERS", "INDUSTRIA"):
            current = saved_fondo.get(serie, auto_base)
            raw = input(f"    {serie:<12} [actual: {current:,.4f}]: ").strip()
            try:
                bases[fondo_label][serie] = float(raw.replace(",", ".")) if raw else current
            except ValueError:
                print(f"    Valor inválido, se usa {current:,.4f}")
                bases[fondo_label][serie] = current

    _save_bases(bases_path, bases)
    print(f"\n  Valores guardados en {bases_path}\n")
    return bases


def cmd_download(cfg: Config, full: bool) -> None:
    client = SodaClient(cfg.app_token, cfg.api_version, cfg.page_size)
    vf = download_valor_fondo(client, cfg, full=full)
    vu = download_valor_unidad(client, cfg, full=full)
    log.info("valor_fondo: %d filas (%s → %s)", len(vf), vf["fecha"].min(), vf["fecha"].max())
    log.info("valor_unidad: %d filas (%s → %s)", len(vu), vu["fecha"].min(), vu["fecha"].max())
    check_vf_coverage(vf, vu)


def cmd_peers(cfg: Config, set_bases: bool = False) -> None:
    d = cfg.data_dir
    vf = pd.read_parquet(d / "valor_fondo.parquet")
    vu = pd.read_parquet(d / "valor_unidad.parquet")
    peers = compute_peers(vu, vf, cfg.afp_foco, cfg.lag_peso, cfg.excluir_patrimonios)
    idx = to_index(peers)
    cov = coverage_report(vu, vf)

    bases_path = cfg.bases_path
    if (set_bases or not bases_path.exists()) and _IS_TTY:
        bases = _prompt_bases(vu, peers, cfg.afp_foco, bases_path)
    else:
        bases = _load_bases(bases_path)
        if bases:
            log.info("Usando bases VU de %s (--set-bases para cambiarlas)", bases_path)

    vu_wide = make_wide_vu(vu, peers, cfg.afp_foco, bases=bases or None)
    vf_wide = make_wide_vf(vf, cfg.afp_foco)

    peers.to_parquet(d / "peers_diario.parquet", index=False)
    peers.to_csv(d / "peers_diario.csv", index=False)
    idx.to_csv(d / "peers_indices.csv", index=False)
    cov.to_csv(d / "cobertura.csv", index=False)
    vu_wide.to_csv(d / "vu_wide.csv", index=False)
    make_melt(vu_wide).to_csv(d / "vu_melt.csv", index=False)
    vf_wide.to_csv(d / "vf_wide.csv", index=False)
    make_melt(vf_wide).to_csv(d / "vf_melt.csv", index=False)
    log.info("Peers de %s: %d filas, %d portafolios, hasta %s. Salidas en %s/",
             cfg.afp_foco_nombre, len(peers), peers["codigo_patrimonio"].nunique(), peers["fecha"].max(), d)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="afp-peers", description="Peers AFP ponderados por valor de fondo")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    dl = sub.add_parser("download", help="Descarga/actualiza valor de fondo y valor de unidad")
    dl.add_argument("--full", action="store_true", help="Re-descarga todo el histórico")
    peers_cmd = sub.add_parser("peers", help="Calcula peers e industria con los datos locales")
    peers_cmd.add_argument("--set-bases", action="store_true",
                           help="Re-pregunta el VU base de PEERS e INDUSTRIA por fondo")
    run = sub.add_parser("run", help="download + peers")
    run.add_argument("--full", action="store_true")
    run.add_argument("--set-bases", action="store_true",
                     help="Re-pregunta el VU base de PEERS e INDUSTRIA por fondo")
    for sp in (peers_cmd, run):
        sp.add_argument("--afp", help="AFP foco por nombre o código (porvenir, proteccion, colfondos, "
                                      "skandia); por defecto la de config.yaml")
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    cfg = Config.load(a.config)
    if getattr(a, "afp", None):
        try:
            cfg.afp_foco = resolve_afp(a.afp)
        except ValueError as e:
            p.error(str(e))
    if a.cmd in ("download", "run"):
        cmd_download(cfg, a.full)
    if a.cmd in ("peers", "run"):
        cmd_peers(cfg, set_bases=getattr(a, "set_bases", False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
