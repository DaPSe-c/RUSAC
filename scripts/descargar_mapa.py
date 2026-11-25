"""Descarga de OpenStreetMap la red peatonal de ESCOM–Zacatenco–Lindavista.

Uso:
    pip install -r requirements-datos.txt
    python scripts/descargar_mapa.py            # margen de 700 m alrededor de los puntos
    python scripts/descargar_mapa.py --margen 1000

Necesita internet solo esta vez. El resultado (data/grafo.json) se usa sin conexión.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from rusac import config as C  # noqa: E402
from rusac.osm_export import graph_to_json  # noqa: E402


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return text.lower()


# Cómo reconocer cada punto de interés en OpenStreetMap
BUSQUEDAS = {
    "escom": {"nombre": "ESCOM (IPN)", "contiene": ["escuela superior de computo", "escom"]},
    "metro_politecnico": {"nombre": "Metro Politécnico", "contiene": ["politecnico"], "metro": True},
    "metro_lindavista": {"nombre": "Metro Lindavista", "contiene": ["lindavista"], "metro": True},
    "metrobus_ipn": {"nombre": "Metrobús IPN", "contiene": ["ipn", "politecnico"], "metrobus": True},
}


def bbox_from_points(points: list[tuple[float, float]], margin_m: float) -> tuple[float, float, float, float]:
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    dlat = margin_m / 110_540
    dlon = margin_m / (111_320 * math.cos(math.radians(sum(lats) / len(lats))))
    return (min(lons) - dlon, min(lats) - dlat, max(lons) + dlon, max(lats) + dlat)  # (oeste, sur, este, norte)


def text_of(row, key: str) -> str:
    """Valor de texto de una columna de OSM (las vacías vienen como NaN)."""
    value = row.get(key, "")
    return value if isinstance(value, str) else ""


def find_pois(ox, bbox) -> dict:
    """Busca los puntos de interés en OSM; si alguno falla usa el respaldo de config.py."""
    pois = {k: dict(v) for k, v in C.POIS_RESPALDO.items()}
    try:
        feats = ox.features_from_bbox(bbox, tags={
            "railway": ["station", "subway_entrance"],
            "public_transport": ["station", "stop_position", "platform"],
            "amenity": ["university", "college", "school"],
            "highway": ["bus_stop"],
        })
    except Exception as exc:  # noqa: BLE001
        print(f"  ! No se pudieron consultar puntos de interés ({exc}); uso coordenadas de respaldo.")
        return pois

    for key, rule in BUSQUEDAS.items():
        best = None
        for _, row in feats.iterrows():
            name = norm(text_of(row, "name"))
            if not name or not any(c in name for c in rule["contiene"]):
                continue
            tags = " ".join(norm(text_of(row, t)) for t in ("network", "operator", "station", "subway", "railway"))
            es_metrobus = "metrobus" in tags
            es_metro = ("metro" in tags.replace("metrobus", "") or "subway" in tags) and not es_metrobus
            if rule.get("metro") and not es_metro:
                continue
            if rule.get("metrobus") and not es_metrobus:
                continue
            if not rule.get("metro") and not rule.get("metrobus") and (es_metro or es_metrobus):
                continue
            pt = row.geometry.centroid
            best = {"nombre": rule["nombre"], "lat": round(pt.y, 6), "lon": round(pt.x, 6), "fuente": "osm"}
            break
        if best:
            pois[key] = best
            print(f"  ✓ {rule['nombre']}: {best['lat']}, {best['lon']}")
        elif key in pois:
            pois[key]["fuente"] = "respaldo (aproximado)"
            print(f"  ~ {rule['nombre']}: no encontrado en OSM, uso coordenada aproximada")
        else:
            print(f"  - {rule['nombre']}: no encontrado, se omite")
    return pois


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--margen", type=float, default=700, help="metros alrededor de los puntos de interés")
    ap.add_argument("--salida", type=Path, default=C.GRAPH_FILE)
    args = ap.parse_args()

    try:
        import osmnx as ox
    except ImportError:
        sys.exit("Falta OSMnx. Instala:  pip install -r requirements-datos.txt")

    pts = [(p["lat"], p["lon"]) for p in C.POIS_RESPALDO.values()]
    bbox = bbox_from_points(pts, args.margen)
    print(f"Descargando red peatonal de OpenStreetMap (bbox {tuple(round(b, 4) for b in bbox)})…")
    G = ox.graph_from_bbox(bbox, network_type="walk", simplify=True, retain_all=False)
    print(f"  {G.number_of_nodes()} nodos, {G.number_of_edges()} aristas dirigidas")

    print("Buscando puntos de interés…")
    pois = find_pois(ox, bbox)

    data = graph_to_json(G, pois, fuente="OpenStreetMap (© colaboradores de OSM, ODbL)")
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"Listo: {args.salida} ({data['meta']['calles']} calles)")


if __name__ == "__main__":
    main()
