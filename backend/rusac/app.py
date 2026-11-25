"""API web de RUSAC (FastAPI) + servidor de la página estática."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config as C
from .graph import load_graph
from .risk import RiskModel
from .routing import RouteError, compute_routes
from .store import Store

LatLon = tuple[float, float]


class RouteIn(BaseModel):
    origen: LatLon
    destino: LatLon
    hora: int | None = Field(default=None, ge=0, le=23)
    alfa: float = Field(default=C.ALFA_DEFECTO, ge=0, le=C.ALFA_MAX)


class ReportIn(BaseModel):
    lat: float
    lon: float
    categoria: str
    posicion: LatLon  # dónde está quien reporta (se valida y NO se guarda)


class ConfirmIn(BaseModel):
    posicion: LatLon


class Broadcaster:
    """Difunde eventos a las páginas abiertas mediante Server-Sent Events."""

    def __init__(self) -> None:
        self.queues: set[asyncio.Queue] = set()

    def publish(self, event: str, data: dict) -> None:
        for q in list(self.queues):
            q.put_nowait((event, data))


def current_hour() -> int:
    return datetime.now(C.TZ).hour


def create_app(graph_path: Path | None = None, db_path: Path | None = None,
               clock=time.time) -> FastAPI:
    graph_path = Path(graph_path or C.GRAPH_FILE)
    db_path = Path(db_path or C.DB_FILE)
    if not graph_path.exists():
        raise SystemExit(
            f"No encuentro el mapa ({graph_path}). Ejecuta primero:  python scripts/descargar_mapa.py"
        )
    wg = load_graph(graph_path)
    store = Store(db_path)
    model = RiskModel(wg, store)
    bus = Broadcaster()

    app = FastAPI(title="RUSAC MVP", version="0.1.0")
    app.state.wg, app.state.store, app.state.model, app.state.bus = wg, store, model, bus

    def session_hash(x_sesion: str | None) -> str:
        if not x_sesion or len(x_sesion) < 8:
            raise HTTPException(400, "Falta el identificador de sesión.")
        return store.hash_session(x_sesion)

    def check_in_area(lat: float, lon: float) -> None:
        _, d = wg.nearest_node(lat, lon)
        if d > 400:
            raise HTTPException(400, "Ese punto está fuera de la zona cubierta.")

    # --- información general ------------------------------------------------
    @app.get("/api/meta")
    def meta():
        lats = [p[0] for p in wg.nodes.values()]
        lons = [p[1] for p in wg.nodes.values()]
        pois = wg.pois or C.POIS_RESPALDO
        presets = []
        if "escom" in pois:
            for key, p in pois.items():
                if key != "escom":
                    presets.append({"id": f"escom-{key}", "nombre": f"ESCOM → {p['nombre']}",
                                    "origen": [pois["escom"]["lat"], pois["escom"]["lon"]],
                                    "destino": [p["lat"], p["lon"]]})
        return {
            "bbox": [min(lats), min(lons), max(lats), max(lons)],
            "centro": [wg.lat0, wg.lon0],
            "pois": pois,
            "trayectos": presets,
            "categorias": {k: {"nombre": v["nombre"], "icono": v["icono"]} for k, v in C.CATEGORIAS.items()},
            "alfa_defecto": C.ALFA_DEFECTO,
            "alfa_max": C.ALFA_MAX,
            "hora_actual": current_hour(),
            "umbral": {"amarillo": C.UMBRAL_AMARILLO, "rojo": C.UMBRAL_ROJO},
            "distancia_max_reporte_m": C.DISTANCIA_MAX_REPORTE_M,
            "mapa_sintetico": bool(wg.meta.get("sintetico")),
            "fuente_mapa": wg.meta.get("fuente", "desconocida"),
            "incidentes": store.incident_sources(),
        }

    @app.get("/api/calles")
    def streets():
        """Geometría de todas las calles (se pide una vez; sirve también como mapa base sin internet)."""
        return [s.geom for s in wg.streets]

    @app.get("/api/riesgo")
    def risk_layer(hora: int | None = Query(default=None, ge=0, le=23)):
        h = current_hour() if hora is None else hora
        r, blocked = model.vector(h, clock())
        return {"hora": h, "riesgo": [round(float(x), 2) for x in r], "bloqueadas": sorted(blocked)}

    # --- rutas ---------------------------------------------------------------
    @app.post("/api/ruta")
    def route(body: RouteIn):
        h = current_hour() if body.hora is None else body.hora
        try:
            return compute_routes(wg, model, tuple(body.origen), tuple(body.destino), h, body.alfa, clock())
        except RouteError as exc:
            raise HTTPException(400, str(exc)) from exc

    # --- reportes ------------------------------------------------------------
    def public(rep: dict) -> dict:
        cat = C.CATEGORIAS[rep["categoria"]]
        return {k: rep[k] for k in ("id", "lat", "lon", "categoria", "edad_min", "confirmaciones",
                                    "verificado", "vigencia", "fuente")} | {"nombre": cat["nombre"],
                                                                            "icono": cat["icono"]}

    @app.get("/api/reportes")
    def reports():
        return [public(r) for r in model.active_reports(clock())]

    @app.post("/api/reportes", status_code=201)
    async def create_report(body: ReportIn, x_sesion: str | None = Header(default=None)):
        if body.categoria not in C.CATEGORIAS:
            raise HTTPException(400, "Categoría desconocida.")
        sh = session_hash(x_sesion)
        check_in_area(body.lat, body.lon)
        if wg.distance_m((body.lat, body.lon), tuple(body.posicion)) > C.DISTANCIA_MAX_REPORTE_M:
            raise HTTPException(
                400, f"Solo puedes reportar a menos de {C.DISTANCIA_MAX_REPORTE_M:.0f} m de donde estás.")
        now = clock()
        if store.recent_report_count(sh, now - C.VENTANA_LIMITE_MIN * 60) >= C.LIMITE_REPORTES:
            raise HTTPException(
                429, f"Límite alcanzado: {C.LIMITE_REPORTES} reportes cada {C.VENTANA_LIMITE_MIN} minutos.")
        rid = store.add_report(body.lat, body.lon, body.categoria, sh, now=now)
        rep = next(r for r in model.active_reports(now) if r["id"] == rid)
        bus.publish("reporte", public(rep))
        return public(rep)

    @app.post("/api/reportes/{rid}/confirmar")
    async def confirm_report(rid: int, body: ConfirmIn, x_sesion: str | None = Header(default=None)):
        sh = session_hash(x_sesion)
        rep = store.get_report(rid)
        if rep is None:
            raise HTTPException(404, "El reporte no existe o ya expiró.")
        if rep["sesion_hash"] == sh:
            raise HTTPException(400, "No puedes confirmar tu propio reporte.")
        if wg.distance_m((rep["lat"], rep["lon"]), tuple(body.posicion)) > C.DISTANCIA_MAX_REPORTE_M:
            raise HTTPException(400, "Para confirmar tienes que estar cerca del reporte.")
        if not store.confirm(rid, sh, now=clock()):
            raise HTTPException(400, "Ya habías confirmado este reporte.")
        act = next((r for r in model.active_reports(clock()) if r["id"] == rid), None)
        if act is None:
            raise HTTPException(404, "El reporte ya expiró.")
        bus.publish("confirmacion", public(act))
        return public(act)

    @app.post("/api/demo/limpiar")
    async def clear_reports():
        """Solo para la demostración: borra todos los reportes."""
        store.clear_reports()
        bus.publish("limpiar", {})
        return {"ok": True}

    # --- eventos en vivo (SSE) -------------------------------------------------
    @app.get("/api/eventos")
    async def events(request: Request):
        q: asyncio.Queue = asyncio.Queue()
        bus.queues.add(q)

        async def stream():
            try:
                yield "retry: 3000\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event, data = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                bus.queues.discard(q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    app.mount("/", StaticFiles(directory=C.FRONTEND_DIR, html=True), name="frontend")
    return app
