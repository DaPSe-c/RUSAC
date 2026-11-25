"""Datos de demostración: mapa sintético e incidentes simulados.

TODO lo que sale de aquí está marcado como 'simulado' o 'sintetico' y la página
lo avisa. Sirve para probar y presentar el MVP mientras se consiguen datos reales.
"""
from __future__ import annotations

import math
import random

import networkx as nx

from . import config as C
from .graph import WalkGraph
from .osm_export import graph_to_json
from .store import Store


# --- mapa sintético (para pruebas sin internet) --------------------------------
def synthetic_osm_graph(spacing_m: float = 110.0, margin_m: float = 450.0, seed: int = 3) -> nx.MultiDiGraph:
    """Cuadrícula tipo OSMnx alrededor de los puntos de respaldo, con andadores diagonales."""
    rnd = random.Random(seed)
    lats = [p["lat"] for p in C.POIS_RESPALDO.values()]
    lons = [p["lon"] for p in C.POIS_RESPALDO.values()]
    lat0 = sum(lats) / len(lats)
    kx = 111_320 * math.cos(math.radians(lat0))
    dlat, dlon = spacing_m / 110_540, spacing_m / kx
    south, west = min(lats) - margin_m / 110_540, min(lons) - margin_m / kx
    rows = int((max(lats) - min(lats) + 2 * margin_m / 110_540) / dlat) + 1
    cols = int((max(lons) - min(lons) + 2 * margin_m / kx) / dlon) + 1

    G = nx.MultiDiGraph(crs="epsg:4326")
    nid = lambda r, c: r * cols + c  # noqa: E731
    for r in range(rows):
        for c in range(cols):
            G.add_node(nid(r, c), y=south + r * dlat, x=west + c * dlon)

    def add(a, b, kind, name):
        ya, xa = G.nodes[a]["y"], G.nodes[a]["x"]
        yb, xb = G.nodes[b]["y"], G.nodes[b]["x"]
        length = math.hypot((ya - yb) * 110_540, (xa - xb) * kx)
        for u, v in ((a, b), (b, a)):
            G.add_edge(u, v, length=length, highway=kind, name=name)

    for r in range(rows):
        for c in range(cols):
            if c + 1 < cols:  # calles este-oeste
                kind, name = ("primary", f"Av. Oriente {r}") if r % 6 == 0 else ("residential", f"Calle {r}")
                add(nid(r, c), nid(r, c + 1), kind, name)
            if r + 1 < rows:  # calles norte-sur
                kind, name = ("secondary", f"Av. Norte {c}") if c % 6 == 0 else ("residential", f"Cerrada {c}")
                add(nid(r, c), nid(r + 1, c), kind, name)
            if r + 1 < rows and c + 1 < cols and rnd.random() < 0.18:  # atajos (andadores / callejones)
                kind = rnd.choice(["footway", "service", "path"])
                a, b = (nid(r, c), nid(r + 1, c + 1)) if rnd.random() < 0.5 else (nid(r, c + 1), nid(r + 1, c))
                add(a, b, kind, "Andador" if kind != "service" else "Callejón")
    return G


def synthetic_graph_json() -> dict:
    pois = {k: dict(v, fuente="respaldo (aproximado)") for k, v in C.POIS_RESPALDO.items()}
    return graph_to_json(synthetic_osm_graph(), pois, fuente="Mapa sintético de prueba (no es el real)",
                         sintetico=True)


# --- incidentes simulados --------------------------------------------------------
def _point_on(street, t: float) -> tuple[float, float]:
    g = street.geom
    if len(g) == 1:
        return tuple(g[0])
    pos = t * (len(g) - 1)
    i = min(int(pos), len(g) - 2)
    f = pos - i
    return (g[i][0] + f * (g[i + 1][0] - g[i][0]), g[i][1] + f * (g[i + 1][1] - g[i][1]))


def _night_hour(rnd: random.Random) -> int:
    return rnd.choice([19, 20, 20, 21, 21, 21, 22, 22, 23, 0]) if rnd.random() < 0.8 else rnd.randrange(24)


def seed_incidents(wg: WalkGraph, store: Store, seed: int = 7) -> int:
    rnd = random.Random(seed)
    rows: list[tuple[float, float, int, str, str]] = []

    def cluster(street, n, hour_fn, jitter_m=18.0):
        for _ in range(n):
            lat, lon = _point_on(street, rnd.uniform(0.2, 0.8))
            lat += rnd.gauss(0, jitter_m) / 110_540
            lon += rnd.gauss(0, jitter_m) / (111_320 * math.cos(math.radians(lat)))
            rows.append((lat, lon, hour_fn(), "robo a transeúnte (simulado)", "simulado"))

    # 1) Puntos rojos nocturnos sobre la ruta más corta de cada trayecto,
    #    para que la demostración muestre la diferencia entre ambas rutas.
    pois = wg.pois or C.POIS_RESPALDO
    if "escom" in pois:
        s, _ = wg.nearest_node(pois["escom"]["lat"], pois["escom"]["lon"])
        for key, p in pois.items():
            if key == "escom":
                continue
            t, _ = wg.nearest_node(p["lat"], p["lon"])
            try:
                path = nx.shortest_path(wg.G, s, t, weight=lambda u, v, d: wg.streets[d["idx"]].length)
            except nx.NetworkXNoPath:
                continue
            used = [wg.G.edges[u, v]["idx"] for u, v in zip(path[:-1], path[1:])]
            calm = [i for i in used if wg.streets[i].kind not in ("primary", "trunk", "secondary")] or used
            for frac in (0.35, 0.65):
                cluster(wg.streets[calm[int(frac * (len(calm) - 1))]], 22, lambda: _night_hour(rnd))

    # 2) Un punto conflictivo de día sobre una avenida (cambia el semáforo por horario)
    avenues = [s for s in wg.streets if s.kind in ("primary", "secondary")]
    if avenues:
        cluster(rnd.choice(avenues), 18, lambda: rnd.choice([13, 14, 14, 15, 16]))

    # 3) Ruido de fondo, más probable en calles chicas
    weights = [C.RIESGO_ESTRUCTURAL.get(s.kind, C.RIESGO_ESTRUCTURAL_DEFECTO) + 0.05 for s in wg.streets]
    for st in rnd.choices(wg.streets, weights=weights, k=160):
        cluster(st, 1, lambda: _night_hour(rnd) if rnd.random() < 0.6 else rnd.randrange(24), jitter_m=10)

    store.add_incidents(rows, replace_source="simulado")
    return len(rows)
