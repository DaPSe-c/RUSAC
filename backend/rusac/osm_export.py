"""Convierte un grafo de OSMnx (MultiDiGraph) al formato compacto data/grafo.json.

Se separa del script de descarga para poder probarlo sin conexión a internet.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _first(value, default=""):
    if isinstance(value, (list, tuple)):
        return value[0] if value else default
    return default if value is None else value


def _name(value) -> str:
    if isinstance(value, (list, tuple)):
        return " / ".join(str(v) for v in value if v)
    return "" if value is None else str(value)


def graph_to_json(G, pois: dict, fuente: str, sintetico: bool = False) -> dict:
    nodes = {str(n): [round(float(d["y"]), 7), round(float(d["x"]), 7)] for n, d in G.nodes(data=True)}
    best: dict[tuple[str, str], dict] = {}
    for u, v, d in G.edges(data=True):
        u, v = str(u), str(v)
        if u == v:
            continue
        key = (u, v) if u < v else (v, u)
        length = float(d.get("length", 0.0))
        if key in best and best[key]["longitud"] <= length:
            continue
        geom = d.get("geometry")
        if geom is not None:
            pts = [[round(y, 7), round(x, 7)] for x, y in geom.coords]
        else:
            pts = [nodes[u], nodes[v]]
        # guardamos siempre la geometría orientada de key[0] a key[1]
        if u != key[0]:
            pts = list(reversed(pts))
        best[key] = {
            "u": key[0],
            "v": key[1],
            "longitud": round(length, 1),
            "tipo": str(_first(d.get("highway"), "unclassified")),
            "nombre": _name(d.get("name")),
            "geom": pts,
        }
    return {
        "meta": {
            "fuente": fuente,
            "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sintetico": sintetico,
            "nodos": len(nodes),
            "calles": len(best),
        },
        "pois": pois,
        "nodos": nodes,
        "aristas": list(best.values()),
    }
