"""Cliente mínimo para la API Socrata (SODA2 y SODA3) de datos.gov.co.

SODA2  -> GET  https://www.datos.gov.co/resource/{id}.json   (token opcional)
SODA3  -> POST https://www.datos.gov.co/api/v3/views/{id}/query.json  (token obligatorio)

Ambos devuelven lo mismo; la diferencia es autenticación y paginación.
"""
from __future__ import annotations

import logging
import time
from typing import Iterator

import requests

log = logging.getLogger(__name__)

BASE = "https://www.datos.gov.co"


class SodaClient:
    def __init__(
        self,
        app_token: str | None = None,
        version: str = "soda2",
        page_size: int = 50_000,
        timeout: int = 120,
        max_retries: int = 5,
    ) -> None:
        version = version.lower()
        if version not in {"soda2", "soda3"}:
            raise ValueError("version debe ser 'soda2' o 'soda3'")
        if version == "soda3" and not app_token:
            raise ValueError("SODA3 requiere app token (variable SOCRATA_APP_TOKEN)")
        self.version = version
        self.page_size = page_size
        self.timeout = timeout
        self.max_retries = max_retries
        self.s = requests.Session()
        if app_token:
            self.s.headers["X-App-Token"] = app_token

    # ------------------------------------------------------------------ http
    def _request(self, method: str, url: str, **kw) -> list[dict]:
        for attempt in range(1, self.max_retries + 1):
            try:
                r = self.s.request(method, url, timeout=self.timeout, **kw)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
                r.raise_for_status()
                return r.json()
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
                if attempt == self.max_retries:
                    raise
                wait = 2**attempt
                log.warning("Error %s (intento %d/%d), reintento en %ds", e, attempt, self.max_retries, wait)
                time.sleep(wait)
        return []  # pragma: no cover

    # ------------------------------------------------------------- paginado
    def iter_pages(
        self, dataset_id: str, select: str = "*", where: str | None = None, order: str = ":id"
    ) -> Iterator[list[dict]]:
        """Itera páginas de registros. `order` estable es obligatorio para paginar bien."""
        page = 0
        while True:
            if self.version == "soda2":
                params = {"$select": select, "$order": order, "$limit": self.page_size,
                          "$offset": page * self.page_size}
                if where:
                    params["$where"] = where
                rows = self._request("GET", f"{BASE}/resource/{dataset_id}.json", params=params)
            else:
                soql = f"SELECT {select}" + (f" WHERE {where}" if where else "") + f" ORDER BY {order}"
                body = {"query": soql,
                        "page": {"pageNumber": page + 1, "pageSize": self.page_size},
                        "includeSynthetic": False}
                rows = self._request("POST", f"{BASE}/api/v3/views/{dataset_id}/query.json", json=body)
            if not rows:
                return
            log.info("%s página %d: %d filas", dataset_id, page + 1, len(rows))
            yield rows
            if len(rows) < self.page_size:
                return
            page += 1

    def fetch_all(self, dataset_id: str, **kw) -> list[dict]:
        out: list[dict] = []
        for rows in self.iter_pages(dataset_id, **kw):
            out.extend(rows)
        return out
