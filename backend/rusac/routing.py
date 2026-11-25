"""Cálculo y comparación de rutas: la más corta contra la más segura.

costo_seguro(calle) = longitud × (1 + α · riesgo(calle, hora))

Con α = 0 ambas rutas coinciden; entre más grande es α, más metros extra
está dispuesto a caminar el alumno para evitar calles riesgosas.
"""
from __future__ import annotations

import time

import networkx as nx
import numpy as np

from . import config as C
from .graph import WalkGraph
from .risk import RiskModel, level

MAX_SNAP_M = 400.0  # si el punto está más lejos que esto de cualquier calle, está fuera de la zona


class RouteError(ValueError):
    pass


def _summary(wg: WalkGraph, path: list[str], risk: np.ndarray, affected: set[int]) -> dict:
    coords, used = wg.path_geometry(path)
    length = float(sum(wg.streets[i].length for i in used))
    exposure = float(sum(wg.streets[i].length * risk[i] for i in used))
    tramos, names = [], []
    for i in used:
        s = wg.streets[i]
        r = float(risk[i])
        geom = s.geom
        tramos.append({"geom": geom, "riesgo": round(r, 3), "nivel": level(r)})
        if s.name and (not names or names[-1] != s.name):
            names.append(s.name)
    return {
        "coords": coords,
        "tramos": tramos,
        "calles": names,
        "longitud_m": round(length),
        "minutos": round(length / C.VELOCIDAD_CAMINATA_M_MIN, 1),
        "exposicion": round(exposure, 1),
        "riesgo_promedio": round(exposure / length, 3) if length else 0.0,
        "riesgo_max": round(max((float(risk[i]) for i in used), default=0.0), 3),
        "tramos_rojos": sum(1 for t in tramos if t["nivel"] == "rojo"),
        "reportes_en_ruta": len(affected.intersection(used)),
        "_path": path,
    }


def compute_routes(wg: WalkGraph, model: RiskModel, origin: tuple[float, float],
                   destination: tuple[float, float], hour: int, alpha: float,
                   now: float | None = None) -> dict:
    alpha = float(min(max(alpha, 0.0), C.ALFA_MAX))
    s, ds = wg.nearest_node(*origin)
    t, dt = wg.nearest_node(*destination)
    if ds > MAX_SNAP_M or dt > MAX_SNAP_M:
        raise RouteError("El origen o el destino está fuera de la zona del mapa.")
    if s == t:
        raise RouteError("El origen y el destino son el mismo punto.")

    now = now or time.time()
    risk, blocked = model.vector(hour, now)
    reps = model.active_reports(now)
    affected: set[int] = set()
    for rep in reps:
        affected.update(model._reach(rep).keys())

    def w_short(u, v, d):
        return None if d["idx"] in blocked else wg.streets[d["idx"]].length

    def w_safe(u, v, d):
        i = d["idx"]
        return None if i in blocked else wg.streets[i].length * (1.0 + alpha * risk[i])

    try:
        p_short = nx.shortest_path(wg.G, s, t, weight=w_short)
        p_safe = nx.shortest_path(wg.G, s, t, weight=w_safe)
    except nx.NetworkXNoPath as exc:
        raise RouteError("No hay camino disponible (¿calles cerradas?).") from exc

    short = _summary(wg, p_short, risk, affected)
    safe = _summary(wg, p_safe, risk, affected)
    same = short.pop("_path") == safe.pop("_path")
    reduction = 0.0
    if short["exposicion"] > 0:
        reduction = max(0.0, (1 - safe["exposicion"] / short["exposicion"]) * 100)
    return {
        "hora": hour,
        "alfa": alpha,
        "corta": short,
        "segura": safe,
        "misma_ruta": same,
        "comparacion": {
            "metros_extra": safe["longitud_m"] - short["longitud_m"],
            "minutos_extra": round(safe["minutos"] - short["minutos"], 1),
            "reduccion_riesgo_pct": round(reduction),
        },
        "calles_bloqueadas": len(blocked),
    }
