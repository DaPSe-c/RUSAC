"""Arranca el MVP de RUSAC con un solo comando.

    python iniciar.py                 # usa data/grafo.json (o lo descarga si tienes OSMnx)
    python iniciar.py --sintetico     # mapa de prueba sin internet
    python iniciar.py --red           # accesible desde tu celular en la misma red Wi-Fi
    python iniciar.py --red --cert cert.pem --key key.pem   # con HTTPS (para usar el GPS del celular)

Luego abre http://localhost:8000
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

from rusac import config as C  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sintetico", action="store_true", help="crea y usa un mapa sintético de prueba")
    ap.add_argument("--red", action="store_true", help="escucha en 0.0.0.0 (otros dispositivos de tu red)")
    ap.add_argument("--puerto", type=int, default=8000)
    ap.add_argument("--cert", help="certificado HTTPS (p. ej. generado con mkcert)")
    ap.add_argument("--key", help="llave privada HTTPS")
    ap.add_argument("--resembrar", action="store_true", help="vuelve a generar los incidentes simulados")
    args = ap.parse_args()

    C.DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.sintetico:
        from rusac.demo import synthetic_graph_json
        if C.GRAPH_FILE.exists() and not json.loads(C.GRAPH_FILE.read_text(encoding="utf-8"))["meta"].get("sintetico"):
            sys.exit("Ya tienes un mapa real en data/grafo.json; muévelo o bórralo si quieres usar el sintético.")
        C.GRAPH_FILE.write_text(json.dumps(synthetic_graph_json()), encoding="utf-8")
        print("Mapa sintético creado (no corresponde a las calles reales).")
    elif not C.GRAPH_FILE.exists():
        print("No hay mapa todavía; intentando descargarlo de OpenStreetMap…")
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "descargar_mapa.py")])
        if r.returncode != 0 or not C.GRAPH_FILE.exists():
            sys.exit("No se pudo descargar el mapa. Revisa tu conexión o usa:  python iniciar.py --sintetico")

    from rusac.graph import load_graph
    from rusac.store import Store
    from rusac.demo import seed_incidents

    store = Store(C.DB_FILE)
    fuentes = store.incident_sources()
    if args.resembrar or not fuentes:
        n = seed_incidents(load_graph(C.GRAPH_FILE), store)
        print(f"Se generaron {n} incidentes SIMULADOS para la demostración.")
        print("  (para usar datos reales: python scripts/importar_fgj.py archivo.csv)")

    import uvicorn
    from rusac.app import create_app

    host = "0.0.0.0" if args.red else "127.0.0.1"
    esquema = "https" if args.cert else "http"
    print(f"\nRUSAC listo → {esquema}://localhost:{args.puerto}   (Ctrl+C para detener)\n")
    uvicorn.run(create_app(), host=host, port=args.puerto, ssl_certfile=args.cert, ssl_keyfile=args.key,
                access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
