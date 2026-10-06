import pandas as pd

from afp_peers.cli import main
from afp_peers.config import Config
from afp_peers.download import download_valor_fondo, download_valor_unidad
from afp_peers.soda import SodaClient


class FakeClient(SodaClient):
    """Simula la API: devuelve filas filtrando por la fecha del $where."""

    def __init__(self, vf_rows, vu_rows, page_size=2):
        super().__init__(page_size=page_size)
        self.data = {"hds9-4524": vf_rows, "uawh-cjvi": vu_rows}
        self.calls = []

    def _request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        ds = "hds9-4524" if "hds9-4524" in url else "uawh-cjvi"
        p = kw["params"]
        since = p["$where"].split(">= '")[1][:10]
        col = "fecha_corte" if ds == "hds9-4524" else "fecha"
        rows = [r for r in self.data[ds] if r[col][:10] >= since]
        return rows[p["$offset"]: p["$offset"] + p["$limit"]]


def _vf(f, e, p, v):
    return {"fecha_corte": f + "T00:00:00.000", "codigo_entidad": str(e), "nombre_entidad": "x",
            "tipo_patrimonio": "1", "nombre_tipo_patrimonio": "x", "codigo_patrimonio": str(p),
            "nombre_patrimonio": "x", "cod_unid_capt": "3", "sum_valor": str(v)}


def _vu(f, e, p, v):
    return {"fecha": f + "T00:00:00.000", "codigo_entidad": str(e), "nombre_entidad": '"x"',
            "codigo_patrimonio": str(p), "nombre_fondo": "x", "valor_unidad": str(v)}


def test_soda_paginacion_y_soda3_body():
    c = SodaClient(page_size=2)
    pages = iter([[1, 2], [3, 4], [5]])
    c._request = lambda *a, **k: next(pages)
    assert c.fetch_all("abc") == [1, 2, 3, 4, 5]

    c3 = SodaClient(app_token="tok", version="soda3", page_size=2)
    seen = []
    c3._request = lambda m, url, **k: (seen.append((m, url, k["json"])), [])[1]
    c3.fetch_all("abc", where="x = 1")
    m, url, body = seen[0]
    assert m == "POST" and url.endswith("/api/v3/views/abc/query.json")
    assert body["query"].startswith("SELECT * WHERE x = 1") and body["page"]["pageNumber"] == 1
    assert c3.s.headers["X-App-Token"] == "tok"


def test_descarga_incremental_y_cli(tmp_path):
    cfg = Config(data_dir=tmp_path, fecha_inicio="2026-01-01", redescarga_dias=1)
    vf = [_vf("2026-01-01", e, 1000, 100) for e in (2, 3)]
    vu = [_vu("2026-01-01", e, 1000, 10) for e in (2, 3)] + [_vu("2026-01-02", 2, 1000, 10.1),
                                                              _vu("2026-01-02", 3, 1000, 10.2)]
    download_valor_fondo(FakeClient(vf, vu), cfg)
    download_valor_unidad(FakeClient(vf, vu), cfg)

    # día nuevo + corrección retroactiva del 02-ene
    vu2 = vu[:3] + [_vu("2026-01-02", 3, 1000, 10.3), _vu("2026-01-03", 3, 1000, 10.4)]
    out = download_valor_unidad(FakeClient(vf, vu2), cfg)
    assert len(out) == 5
    row = out[(out["codigo_entidad"] == 3) & (out["fecha"] == pd.Timestamp("2026-01-02"))]
    assert row["valor_unidad"].item() == 10.3

    (tmp_path / "c.yaml").write_text(f"data_dir: {tmp_path}\n")
    assert main(["--config", str(tmp_path / "c.yaml"), "peers"]) == 0
    res = pd.read_csv(tmp_path / "peers_diario.csv")
    assert {"ret_foco", "ret_peers", "ret_industria"} <= set(res.columns)
    assert (tmp_path / "cobertura.csv").exists()
