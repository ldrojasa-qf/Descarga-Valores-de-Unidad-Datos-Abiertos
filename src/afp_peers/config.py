from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Config:
    api_version: str = "soda2"
    page_size: int = 50_000
    datasets: dict = field(default_factory=lambda: {"valor_fondo": "hds9-4524", "valor_unidad": "uawh-cjvi"})
    valor_fondo_filtro: dict = field(default_factory=lambda: {"codigo_columna": "2", "cod_renglon": "110"})
    fecha_inicio: str = "2015-01-01"
    redescarga_dias: int = 45
    afp_foco: int = 3
    lag_peso: int = 1
    excluir_patrimonios: list = field(default_factory=list)
    data_dir: Path = Path("data")
    app_token: str | None = None

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> "Config":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
        api, peers = raw.get("api", {}), raw.get("peers", {})
        base = cls()
        return cls(
            api_version=os.getenv("SODA_VERSION", api.get("version", base.api_version)),
            page_size=int(api.get("page_size", base.page_size)),
            datasets=raw.get("datasets", base.datasets),
            valor_fondo_filtro=raw.get("valor_fondo_filtro", base.valor_fondo_filtro),
            fecha_inicio=str(raw.get("fecha_inicio", base.fecha_inicio)),
            redescarga_dias=int(raw.get("redescarga_dias", base.redescarga_dias)),
            afp_foco=int(peers.get("afp_foco", base.afp_foco)),
            lag_peso=int(peers.get("lag_peso", base.lag_peso)),
            excluir_patrimonios=list(peers.get("excluir_patrimonios") or []),
            data_dir=Path(raw.get("data_dir", "data")),
            app_token=os.getenv("SOCRATA_APP_TOKEN") or None,
        )
