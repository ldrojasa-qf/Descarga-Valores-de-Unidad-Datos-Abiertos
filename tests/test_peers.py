import numpy as np
import pandas as pd
import pytest

from afp_peers.download import normalize_valor_fondo, normalize_valor_unidad
from afp_peers.peers import compute_peers, resolve_afp, to_index


def _vu(rows):
    return pd.DataFrame(rows, columns=["fecha", "codigo_entidad", "codigo_patrimonio", "valor_unidad"]).assign(
        fecha=lambda d: pd.to_datetime(d["fecha"]), nombre_entidad="x", nombre_fondo="x")


def _vf(rows):
    return pd.DataFrame(rows, columns=["fecha", "codigo_entidad", "codigo_patrimonio", "valor_fondo"]).assign(
        fecha=lambda d: pd.to_datetime(d["fecha"]))


@pytest.fixture
def data():
    # Portafolio 1000: Porvenir (3) +1%, peer A (2) +2% con AUM 100, peer B (10) +4% con AUM 300
    vu = _vu([
        ("2026-01-01", 3, 1000, 100.0), ("2026-01-02", 3, 1000, 101.0),
        ("2026-01-01", 2, 1000, 100.0), ("2026-01-02", 2, 1000, 102.0),
        ("2026-01-01", 10, 1000, 100.0), ("2026-01-02", 10, 1000, 104.0),
        # Portafolio 1 (cesantías): solo un peer
        ("2026-01-01", 3, 1, 50.0), ("2026-01-02", 3, 1, 50.5),
        ("2026-01-01", 2, 1, 50.0), ("2026-01-02", 2, 1, 51.0),
    ])
    vf = _vf([
        ("2026-01-01", 3, 1000, 600.0), ("2026-01-02", 3, 1000, 606.0),
        ("2026-01-01", 2, 1000, 100.0), ("2026-01-02", 2, 1000, 9_999.0),  # peso t (lag 0) muy distinto
        ("2026-01-01", 10, 1000, 300.0), ("2026-01-02", 10, 1000, 312.0),
        ("2026-01-01", 3, 1, 10.0), ("2026-01-01", 2, 1, 20.0),
    ])
    return vu, vf


def test_peers_por_portafolio_lag1(data):
    out = compute_peers(*data, afp_foco=3, lag_peso=1).set_index("codigo_patrimonio")
    m = out.loc[1000]
    assert m["ret_foco"] == pytest.approx(0.01)
    assert m["ret_peers"] == pytest.approx((100 * 0.02 + 300 * 0.04) / 400)  # 3.5%
    assert m["ret_industria"] == pytest.approx((600 * 0.01 + 100 * 0.02 + 300 * 0.04) / 1000)
    assert m["n_peers"] == 2 and m["n_industria"] == 3
    assert m["exceso_vs_peers"] == pytest.approx(0.01 - 0.035)
    # cesantías se calcula aparte, con sus propios pesos
    assert out.loc[1, "ret_peers"] == pytest.approx(0.02)


def test_lag0_usa_peso_mismo_dia(data):
    out = compute_peers(*data, afp_foco=3, lag_peso=0).set_index("codigo_patrimonio")
    exp = (9_999 * 0.02 + 312 * 0.04) / (9_999 + 312)
    assert out.loc[1000, "ret_peers"] == pytest.approx(exp)
    # cesantías no tiene VF el 02-ene: tolerancia asof toma el último dato (01-ene)
    assert out.loc[1, "ret_peers"] == pytest.approx(0.02)


def test_peer_sin_peso_se_excluye():
    vu = _vu([("2026-01-01", 3, 1000, 1.0), ("2026-01-02", 3, 1000, 1.01),
              ("2026-01-01", 2, 1000, 1.0), ("2026-01-02", 2, 1000, 1.02),
              ("2026-01-01", 9, 1000, 1.0), ("2026-01-02", 9, 1000, 1.50)])
    vf = _vf([("2026-01-01", 3, 1000, 5.0), ("2026-01-01", 2, 1000, 5.0)])  # Skandia (9) sin VF
    out = compute_peers(vu, vf, afp_foco=3).iloc[0]
    assert out["ret_peers"] == pytest.approx(0.02) and out["n_peers"] == 1


def test_indice_base_100(data):
    idx = to_index(compute_peers(*data)).set_index("codigo_patrimonio")
    # por defecto lag_peso=0: pesos del cierre del mismo día (9_999 y 312)
    esperado = (0.02 * 9_999 + 0.04 * 312) / (9_999 + 312)
    assert idx.loc[1000, "idx_peers"] == pytest.approx(100 * (1 + esperado))


def test_cierre_por_nombre_toma_renglon_mayor():
    def r_ces(p, ren, v):
        return {"fecha_corte": "2026-01-02T00:00:00.000", "codigo_entidad": "3", "nombre_entidad": "Porvenir",
                "tipo_patrimonio": "1", "nombre_tipo_patrimonio": "x", "codigo_patrimonio": str(p),
                "nombre_patrimonio": "x", "cod_unid_capt": "3", "cod_renglon": str(ren),
                "nombre_renglon": "VALOR DEL PORTAFOLIO AL CIERRE DEL", "sum_valor": str(v)}

    def r_pen(p, ren, v):
        # Pensiones: nombre_renglon es "VALOR DEL FONDO AL CIERRE DEL DÍA -..." (igual para 300 y 305)
        return {"fecha_corte": "2026-01-02T00:00:00.000", "codigo_entidad": "3", "nombre_entidad": "Porvenir",
                "tipo_patrimonio": "3", "nombre_tipo_patrimonio": "x", "codigo_patrimonio": str(p),
                "nombre_patrimonio": "x", "cod_unid_capt": "4", "cod_renglon": str(ren),
                "nombre_renglon": "VALOR DEL FONDO AL CIERRE DEL DÍA - ANTES DE ABONO" if ren == 300
                else "VALOR DEL FONDO AL CIERRE DEL DÍA - DESPUES DE ABONO",
                "sum_valor": str(v)}

    vf = normalize_valor_fondo(pd.DataFrame([
        r_ces(1, 105, 100.0), r_ces(1, 110, 101.0),    # cesantías LP: 110 = después de rendimientos
        r_pen(1000, 300, 500.0), r_pen(1000, 305, 499.0),  # pensiones: 305 = después (ret negativo)
        r_pen(7000, 300, 80.0), r_pen(7000, 305, 80.5),    # retiro programado: 305 = después (ret positivo)
    ])).set_index("codigo_patrimonio")

    # cesantías: debe quedar el renglón 110
    assert vf.loc[1, "valor_fondo"] == 101.0
    assert vf.loc[1, "cod_renglon"] == 110
    # pensiones moderado: debe quedar el renglón 305 (aunque su valor sea menor que 300)
    assert vf.loc[1000, "valor_fondo"] == 499.0
    assert vf.loc[1000, "cod_renglon"] == 305
    # retiro programado: renglón 305
    assert vf.loc[7000, "valor_fondo"] == 80.5
    assert vf.loc[7000, "cod_renglon"] == 305


def test_normalizacion_api():
    raw_vf = pd.DataFrame([{"fecha_corte": "2015-01-01T00:00:00.000", "codigo_entidad": "10",
                            "nombre_entidad": '"Colfondos S.A." Y "Colfondos"', "tipo_patrimonio": "5",
                            "nombre_tipo_patrimonio": "FONDO DE CESANTIA", "codigo_patrimonio": "1",
                            "nombre_patrimonio": "FONDO DE CESANTIAS LEY 50", "cod_unid_capt": "3",
                            "sum_valor": "738563489980.8"}])
    vf = normalize_valor_fondo(raw_vf)
    assert vf.loc[0, "valor_fondo"] == 738563489980.8
    assert vf.loc[0, "nombre_entidad"] == "Colfondos S.A. Y Colfondos"
    raw_vu = pd.DataFrame([{"fecha": "2016-01-01T00:00:00.000", "codigo_entidad": "3",
                            "nombre_entidad": '"Porvenir"', "codigo_patrimonio": "1000",
                            "nombre_fondo": "Fondo de Pensiones Moderado", "valor_unidad": "34476.390000"}])
    vu = normalize_valor_unidad(raw_vu)
    assert vu.loc[0, "codigo_patrimonio"] == 1000 and vu.loc[0, "nombre_entidad"] == "Porvenir"
    assert np.issubdtype(vu["fecha"].dtype, np.datetime64)


def test_resolve_afp_por_nombre_o_codigo():
    assert resolve_afp(3) == resolve_afp("3") == resolve_afp("porvenir") == 3
    assert resolve_afp("Protección") == resolve_afp("PRO") == 2
    assert resolve_afp(" colfondos ") == 10 and resolve_afp("Skandia") == resolve_afp("OLD") == 9
    with pytest.raises(ValueError):
        resolve_afp("otra")


def test_cambiar_foco_cambia_peers(data):
    # foco = peer A (2): sus peers son Porvenir (+1%, VF 606) y peer B (+4%, VF 312)
    m = compute_peers(*data, afp_foco=2).set_index("codigo_patrimonio").loc[1000]
    assert m["ret_foco"] == pytest.approx(0.02)
    assert m["ret_peers"] == pytest.approx((0.01 * 606 + 0.04 * 312) / (606 + 312))
