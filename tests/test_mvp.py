"""Pruebas del MVP con un mapa sintético (no requieren internet).

    pip install -r requirements-dev.txt
    pytest
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from rusac import config as C  # noqa: E402
from rusac.app import create_app  # noqa: E402
from rusac.demo import seed_incidents, synthetic_graph_json, synthetic_osm_graph  # noqa: E402
from rusac.graph import load_graph  # noqa: E402
from rusac.osm_export import graph_to_json  # noqa: E402
from rusac.risk import RiskModel  # noqa: E402
from rusac.routing import compute_routes  # noqa: E402
from rusac.store import Store  # noqa: E402

P = C.POIS_RESPALDO
ESCOM = (P["escom"]["lat"], P["escom"]["lon"])
LINDAVISTA = (P["metro_lindavista"]["lat"], P["metro_lindavista"]["lon"])


@pytest.fixture()
def env(tmp_path):
    gpath = tmp_path / "grafo.json"
    gpath.write_text(json.dumps(synthetic_graph_json()))
    wg = load_graph(gpath)
    store = Store(tmp_path / "db.sqlite3")
    return gpath, tmp_path / "db.sqlite3", wg, store


# --- exportación desde OSMnx -------------------------------------------------------
def test_export_dedupes_bidirectional_edges():
    G = synthetic_osm_graph()
    data = graph_to_json(G, {}, "prueba")
    assert data["meta"]["calles"] == G.number_of_edges() // 2
    e = data["aristas"][0]
    assert e["geom"][0] == data["nodos"][e["u"]] and e["geom"][-1] == data["nodos"][e["v"]]


def test_export_handles_osmnx_lists_and_geometry():
    import networkx as nx
    from shapely.geometry import LineString

    G = nx.MultiDiGraph()
    G.add_node(1, y=19.50, x=-99.15)
    G.add_node(2, y=19.51, x=-99.14)
    geom = LineString([(-99.15, 19.50), (-99.145, 19.505), (-99.14, 19.51)])
    G.add_edge(2, 1, length=1500.0, highway=["footway", "path"], name=["A", "B"], geometry=LineString(geom.coords[::-1]))
    G.add_edge(1, 2, length=1500.0, highway=["footway", "path"], name=["A", "B"], geometry=geom)
    e = graph_to_json(G, {}, "prueba")["aristas"][0]
    assert e["tipo"] == "footway" and e["nombre"] == "A / B" and len(e["geom"]) == 3
    assert e["geom"][0] == [19.50, -99.15]  # orientada de u ('1') a v ('2')


# --- modelo de riesgo y rutas ------------------------------------------------------------
def test_without_risk_both_routes_are_the_shortest(env):
    _, _, wg, store = env
    model = RiskModel(wg, store)
    r = compute_routes(wg, model, ESCOM, LINDAVISTA, hour=14, alpha=0)
    assert r["misma_ruta"]
    assert r["comparacion"]["metros_extra"] == 0


def test_safe_route_avoids_hotspot_at_night(env):
    _, _, wg, store = env
    seed_incidents(wg, store)
    model = RiskModel(wg, store)
    r = compute_routes(wg, model, ESCOM, LINDAVISTA, hour=21, alpha=4)
    assert not r["misma_ruta"]
    assert r["segura"]["exposicion"] < r["corta"]["exposicion"]
    assert r["segura"]["longitud_m"] >= r["corta"]["longitud_m"]
    assert r["comparacion"]["reduccion_riesgo_pct"] > 30


def test_risk_depends_on_hour(env):
    _, _, wg, store = env
    seed_incidents(wg, store)
    model = RiskModel(wg, store)
    night, _ = model.vector(21)
    day, _ = model.vector(10)
    assert night.mean() > day.mean()


def test_reports_decay_and_expire(env):
    _, _, wg, store = env
    model = RiskModel(wg, store)
    now = time.time()
    store.add_report(*ESCOM, "sospechosa", "x", now=now)
    fresh, _ = model.vector(12, now)
    later, _ = model.vector(12, now + 45 * 60)          # una vida media (τ)
    gone, _ = model.vector(12, now + 4 * 45 * 60)        # > 3τ → expiró
    assert fresh.max() > later.max() > gone.max()
    assert not model.active_reports(now + 4 * 45 * 60)


def test_verified_closure_blocks_street(env):
    _, _, wg, store = env
    model = RiskModel(wg, store)
    rid = store.add_report(*ESCOM, "cerrada", "autor")
    _, blocked = model.vector(12)
    assert not blocked                     # sin verificar: solo penaliza
    store.confirm(rid, "otra1")
    store.confirm(rid, "otra2")
    _, blocked = model.vector(12)
    assert blocked                         # verificada: se excluye


# --- API -------------------------------------------------------------------------------------
@pytest.fixture()
def client(env):
    gpath, dbpath, _, _ = env
    return TestClient(create_app(gpath, dbpath))


def test_api_meta_and_route(client):
    meta = client.get("/api/meta").json()
    assert meta["mapa_sintetico"] and len(meta["trayectos"]) == 2
    t = meta["trayectos"][0]
    r = client.post("/api/ruta", json={"origen": t["origen"], "destino": t["destino"], "hora": 21, "alfa": 4})
    assert r.status_code == 200 and r.json()["segura"]["coords"]


def test_api_route_outside_area(client):
    r = client.post("/api/ruta", json={"origen": [19.0, -99.0], "destino": list(LINDAVISTA)})
    assert r.status_code == 400 and "fuera" in r.json()["detail"]


def test_api_report_must_be_near_and_is_rate_limited(client):
    h = {"X-Sesion": "sesion-de-prueba-1"}
    far = [ESCOM[0] + 0.01, ESCOM[1]]  # ~1.1 km
    r = client.post("/api/reportes", headers=h, json={"lat": ESCOM[0], "lon": ESCOM[1], "categoria": "alumbrado", "posicion": far})
    assert r.status_code == 400
    for _ in range(C.LIMITE_REPORTES):
        r = client.post("/api/reportes", headers=h, json={"lat": ESCOM[0], "lon": ESCOM[1], "categoria": "alumbrado", "posicion": list(ESCOM)})
        assert r.status_code == 201
    r = client.post("/api/reportes", headers=h, json={"lat": ESCOM[0], "lon": ESCOM[1], "categoria": "alumbrado", "posicion": list(ESCOM)})
    assert r.status_code == 429


def test_api_confirmations(client):
    a, b, c = ({"X-Sesion": f"sesion-{n}-xxxxxxxx"} for n in "abc")
    body = {"lat": ESCOM[0], "lon": ESCOM[1], "categoria": "sospechosa", "posicion": list(ESCOM)}
    rid = client.post("/api/reportes", headers=a, json=body).json()["id"]
    url = f"/api/reportes/{rid}/confirmar"
    assert client.post(url, headers=a, json={"posicion": list(ESCOM)}).status_code == 400  # propio
    assert client.post(url, headers=b, json={"posicion": list(ESCOM)}).json()["confirmaciones"] == 1
    assert client.post(url, headers=b, json={"posicion": list(ESCOM)}).status_code == 400  # repetido
    rep = client.post(url, headers=c, json={"posicion": list(ESCOM)}).json()
    assert rep["verificado"]


def test_report_changes_safe_route(client):
    meta = client.get("/api/meta").json()
    t = next(x for x in meta["trayectos"] if "lindavista" in x["id"])
    body = {"origen": t["origen"], "destino": t["destino"], "hora": 14, "alfa": 6}
    before = client.post("/api/ruta", json=body).json()["segura"]
    # reportes de actividad sospechosa justo a mitad de la ruta segura actual
    mid = before["coords"][len(before["coords"]) // 2]
    for n in range(3):
        h = {"X-Sesion": f"testigo-{n}-xxxxxxxx"}
        client.post("/api/reportes", headers=h, json={"lat": mid[0], "lon": mid[1], "categoria": "sospechosa", "posicion": mid})
    after = client.post("/api/ruta", json=body).json()["segura"]
    assert after["coords"] != before["coords"]


def test_no_user_location_is_stored(client, env):
    _, dbpath, _, _ = env
    h = {"X-Sesion": "privacidad-xxxxxxxx"}
    me = [ESCOM[0] + 0.0005, ESCOM[1]]
    client.post("/api/reportes", headers=h, json={"lat": ESCOM[0], "lon": ESCOM[1], "categoria": "alumbrado", "posicion": me})
    import sqlite3
    db = sqlite3.connect(dbpath)
    cols = [r[1] for r in db.execute("PRAGMA table_info(reportes)")]
    assert "posicion" not in " ".join(cols)
    row = db.execute("SELECT * FROM reportes").fetchone()
    assert me[0] not in row and "privacidad-xxxxxxxx" not in row
