"""Grafo peatonal de la zona y utilidades geométricas.

El grafo se carga desde data/grafo.json (generado con OSMnx). Cada calle se
guarda una sola vez (no dirigida) y se usa en ambos sentidos para las rutas.
Para distancias usamos una proyección local equirectangular en metros: en una
zona de ~3 km el error es despreciable y evita dependencias pesadas.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import numpy as np

M_POR_GRADO_LAT = 110_540.0
M_POR_GRADO_LON_ECUADOR = 111_320.0


@dataclass
class Street:
    idx: int
    u: str
    v: str
    length: float
    kind: str
    name: str
    geom: list[list[float]]  # [[lat, lon], ...] de u a v


@dataclass
class WalkGraph:
    meta: dict
    pois: dict
    nodes: dict[str, tuple[float, float]]
    streets: list[Street]
    G: nx.Graph = field(init=False)
    lat0: float = field(init=False)
    lon0: float = field(init=False)

    def __post_init__(self) -> None:
        lats = [p[0] for p in self.nodes.values()]
        lons = [p[1] for p in self.nodes.values()]
        self.lat0 = sum(lats) / len(lats)
        self.lon0 = sum(lons) / len(lons)
        self._kx = M_POR_GRADO_LON_ECUADOR * math.cos(math.radians(self.lat0))

        # Grafo no dirigido: caminar no tiene sentido de circulación.
        # Si hay dos calles entre los mismos nodos, se queda la más corta.
        self.G = nx.Graph()
        for nid, (lat, lon) in self.nodes.items():
            self.G.add_node(nid, lat=lat, lon=lon)
        for s in self.streets:
            if s.u == s.v:
                continue
            prev = self.G.get_edge_data(s.u, s.v)
            if prev is None or s.length < self.streets[prev["idx"]].length:
                self.G.add_edge(s.u, s.v, idx=s.idx)

        # Índices para búsquedas espaciales vectorizadas
        self._node_ids = list(self.nodes.keys())
        self._node_xy = np.array([self.to_xy(*self.nodes[n]) for n in self._node_ids])
        a, b, owner = [], [], []
        for s in self.streets:
            pts = [self.to_xy(lat, lon) for lat, lon in s.geom]
            for p, q in zip(pts[:-1], pts[1:]):
                a.append(p)
                b.append(q)
                owner.append(s.idx)
        self._seg_a = np.array(a, dtype=float).reshape(-1, 2)
        self._seg_b = np.array(b, dtype=float).reshape(-1, 2)
        self._seg_owner = np.array(owner, dtype=int)

    # --- geometría -----------------------------------------------------------
    def to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        return ((lon - self.lon0) * self._kx, (lat - self.lat0) * M_POR_GRADO_LAT)

    def distance_m(self, a: tuple[float, float], b: tuple[float, float]) -> float:
        ax, ay = self.to_xy(*a)
        bx, by = self.to_xy(*b)
        return math.hypot(ax - bx, ay - by)

    def nearest_node(self, lat: float, lon: float) -> tuple[str, float]:
        p = np.array(self.to_xy(lat, lon))
        d = np.hypot(*(self._node_xy - p).T)
        i = int(d.argmin())
        return self._node_ids[i], float(d[i])

    def iter_street_distances(self, points_xy: np.ndarray, chunk_size: int = 32):
        """Recorre los puntos por bloques y entrega (inicio, distancias (P, n_calles)).

        Por bloques para no reventar la memoria con miles de incidentes.
        """
        pts = np.atleast_2d(points_xy).astype(float)
        ab = self._seg_b - self._seg_a                          # (S,2)
        ab2 = np.maximum((ab ** 2).sum(1), 1e-9)                # (S,)
        for start in range(0, len(pts), chunk_size):
            chunk = pts[start:start + chunk_size]               # (P,2)
            ap = chunk[:, None, :] - self._seg_a[None, :, :]    # (P,S,2)
            t = np.clip((ap * ab[None]).sum(2) / ab2[None], 0, 1)
            proj = self._seg_a[None] + t[..., None] * ab[None]
            d = np.hypot(*(chunk[:, None, :] - proj).transpose(2, 0, 1))  # (P,S) por segmento
            block = np.full((len(self.streets), len(chunk)), np.inf)
            np.minimum.at(block, self._seg_owner, d.T)          # mínimo por calle
            yield start, block.T

    def street_distances(self, points_xy: np.ndarray) -> np.ndarray:
        """Distancia mínima (m) de cada punto a cada calle. Forma: (n_puntos, n_calles)."""
        return np.vstack([d for _, d in self.iter_street_distances(points_xy)])

    def streets_near(self, lat: float, lon: float, radius: float) -> dict[int, float]:
        d = self.street_distances(np.array([self.to_xy(lat, lon)]))[0]
        idx = np.nonzero(d <= radius)[0]
        return {int(i): float(d[i]) for i in idx}

    # --- utilidades ----------------------------------------------------------
    def path_geometry(self, path: list[str]) -> tuple[list[list[float]], list[int]]:
        """Une la geometría de las calles de una ruta, orientada en el sentido del recorrido."""
        coords: list[list[float]] = []
        used: list[int] = []
        for u, v in zip(path[:-1], path[1:]):
            s = self.streets[self.G.edges[u, v]["idx"]]
            seg = s.geom if s.u == u else list(reversed(s.geom))
            coords.extend(seg if not coords else seg[1:])
            used.append(s.idx)
        if not coords and path:
            lat, lon = self.nodes[path[0]]
            coords = [[lat, lon]]
        return coords, used


def load_graph(path: Path) -> WalkGraph:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    nodes = {str(k): (float(v[0]), float(v[1])) for k, v in data["nodos"].items()}
    streets = [
        Street(
            idx=i,
            u=str(e["u"]),
            v=str(e["v"]),
            length=float(e["longitud"]),
            kind=str(e.get("tipo") or "unclassified"),
            name=str(e.get("nombre") or ""),
            geom=[[float(a), float(b)] for a, b in e["geom"]],
        )
        for i, e in enumerate(data["aristas"])
    ]
    return WalkGraph(meta=data.get("meta", {}), pois=data.get("pois", {}), nodes=nodes, streets=streets)
