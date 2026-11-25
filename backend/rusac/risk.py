"""Modelo de riesgo por calle y por hora.

riesgo(calle, hora) = W_HIST·histórico(calle, hora)
                    + W_EST·estructura(calle, hora)
                    + W_REP·Σ reportes_activos · e^(−t/τ)
recortado al intervalo [0, 1].

Es una regla simple y explicable, pensada para el MVP. Más adelante se puede
sustituir el término histórico por un modelo de ML sin tocar el resto.
"""
from __future__ import annotations

import math
import time

import numpy as np

from . import config as C
from .graph import WalkGraph
from .store import Store


def level(r: float) -> str:
    if r >= C.UMBRAL_ROJO:
        return "rojo"
    if r >= C.UMBRAL_AMARILLO:
        return "amarillo"
    return "verde"


class RiskModel:
    def __init__(self, wg: WalkGraph, store: Store) -> None:
        self.wg = wg
        self.store = store
        n = len(wg.streets)
        self.lengths = np.array([s.length for s in wg.streets])
        self.structural = np.array(
            [C.RIESGO_ESTRUCTURAL.get(s.kind, C.RIESGO_ESTRUCTURAL_DEFECTO) for s in wg.streets]
        )
        self.historical = np.zeros((n, 24))
        self._report_reach: dict[int, dict[int, float]] = {}
        self._cache: dict[tuple, tuple[np.ndarray, set[int]]] = {}
        self.rebuild_historical()

    # --- histórico -----------------------------------------------------------
    def rebuild_historical(self) -> None:
        rows = self.store.incidents()
        n = len(self.wg.streets)
        counts = np.zeros((n, 24))
        if rows:
            pts = np.array([self.wg.to_xy(r["lat"], r["lon"]) for r in rows])
            hours = np.array([int(r["hora"]) for r in rows])
            k = C.SUAVIZADO_HORAS
            for start, dist in self.wg.iter_street_distances(pts, chunk_size=64):  # dist: (P, E)
                w = np.clip(1.0 - dist / C.RADIO_INCIDENTE_M, 0.0, None)          # influencia lineal
                hrs = hours[start:start + len(dist)]
                for off in range(-k, k + 1):
                    hw = 1.0 - abs(off) / (k + 1)                       # triangular en el tiempo
                    cols = (hrs + off) % 24
                    # suma la influencia de cada incidente en la columna de su hora
                    np.add.at(counts.T, cols, hw * w)
        density = counts / np.maximum(self.lengths, 30.0)[:, None] * 100.0  # por cada 100 m
        positive = density[density > 0]
        ref = np.percentile(positive, C.PERCENTIL_NORMALIZACION) if positive.size else 1.0
        self.historical = np.clip(density / max(ref, 1e-9), 0.0, 1.0)
        self._cache.clear()

    # --- reportes -----------------------------------------------------------
    def _reach(self, rep: dict) -> dict[int, float]:
        rid = rep["id"]
        if rid not in self._report_reach:
            self._report_reach[rid] = self.wg.streets_near(rep["lat"], rep["lon"], C.RADIO_REPORTE_M)
        return self._report_reach[rid]

    def active_reports(self, now: float) -> list[dict]:
        max_life = max(c["vida_min"] for c in C.CATEGORIAS.values()) * 3 * 60
        out = []
        for rep in self.store.reports_since(now - max_life):
            cat = C.CATEGORIAS.get(rep["categoria"])
            if not cat:
                continue
            age_min = max(0.0, (now - rep["creado"]) / 60.0)
            if age_min > 3 * cat["vida_min"]:
                continue
            rep["edad_min"] = round(age_min, 1)
            rep["vigencia"] = round(math.exp(-age_min / cat["vida_min"]), 3)
            rep["verificado"] = rep["confirmaciones"] >= C.CONFIRMACIONES_PARA_VERIFICAR
            out.append(rep)
        return out

    def report_component(self, now: float) -> tuple[np.ndarray, set[int], list[dict]]:
        n = len(self.wg.streets)
        comp = np.zeros(n)
        blocked: set[int] = set()
        reps = self.active_reports(now)
        for rep in reps:
            cat = C.CATEGORIAS[rep["categoria"]]
            factor = 1.0 if rep["verificado"] else C.FACTOR_SIN_VERIFICAR
            reach = self._reach(rep)
            if cat["bloquea"]:
                if rep["verificado"]:
                    blocked.update(reach.keys())
                    continue
                weight = 0.8  # cierre sin verificar: penaliza fuerte, pero no bloquea
            else:
                weight = cat["peso"]
            for idx, d in reach.items():
                influence = 1.0 - 0.5 * d / C.RADIO_REPORTE_M  # 1 en el punto, 0.5 en el borde
                comp[idx] += weight * rep["vigencia"] * factor * influence
        return comp, blocked, reps

    # --- total ---------------------------------------------------------------
    def vector(self, hour: int, now: float | None = None) -> tuple[np.ndarray, set[int]]:
        now = now or time.time()
        key = (hour, self.store.version, int(now // 60))
        if key not in self._cache:
            night = hour in C.HORAS_NOCHE
            struct = self.structural * (1.0 if night else C.FACTOR_ESTRUCTURAL_DIA)
            rep, blocked, _ = self.report_component(now)
            r = C.W_HIST * self.historical[:, hour] + C.W_EST * struct + C.W_REP * rep
            if len(self._cache) > 64:
                self._cache.clear()
            self._cache[key] = (np.clip(r, 0.0, 1.0), blocked)
        return self._cache[key]
