# RUSAC · MVP local

**Rutas Seguras y Alertas Comunitarias**: *seguridad compartida, decisiones más claras.*

Prototipo que corre en el navegador de tu computadora. Compara la **ruta más corta** con la **ruta más segura** para caminar entre ESCOM, el Metro y los paraderos de Zacatenco. Recalcula la ruta en vivo cuando la comunidad reporta incidentes.

> ⚠️ Es un prototipo académico. **No sustituye al 911.**

---

## Inicio rápido

Requisitos: **Python 3.10 o más reciente**.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements-datos.txt  # incluye OSMnx para descargar el mapa
python iniciar.py
```

Abre **http://localhost:8000**.

La primera vez, `iniciar.py` hace lo siguiente:

1. Descarga la red peatonal real de OpenStreetMap con `scripts/descargar_mapa.py`. Es la **única vez que necesitas internet**; el resultado se guarda en `data/grafo.json`.
2. Genera incidentes **simulados** para que el mapa no esté vacío (la página lo avisa).
3. Arranca el servidor.

**Sin internet** puedes usar un mapa sintético de prueba (una cuadrícula que no corresponde a las calles reales):

```bash
pip install -r requirements.txt
python iniciar.py --sintetico
```

### Opciones de `iniciar.py`

| Opción | Para qué |
|---|---|
| `--sintetico` | Mapa de prueba sin internet |
| `--resembrar` | Vuelve a generar los incidentes simulados |
| `--red` | Permite abrir la página desde otro dispositivo de tu red Wi-Fi |
| `--cert` / `--key` | HTTPS (ver la sección del celular) |
| `--puerto 8080` | Cambia el puerto |

---

## Guion de demostración (unos 3 minutos)

1. **La idea.** Con el trayecto *ESCOM → Metro Lindavista*, mueve la hora a **21:00**. Las tarjetas comparan las dos rutas, por ejemplo: "Caminando **+190 m** evitas **78 %** del riesgo". La ruta verde es la segura y la punteada es la más corta.
2. **El semáforo por horario.** Activa *Semáforo de riesgo por calle* y mueve la hora de 14:00 a 21:00: los colores de las calles cambian.
3. **La prioridad.** Mueve *¿Cuánto priorizas la seguridad?*. En 0 solo se calcula la ruta más corta; al subirlo, la ruta acepta más metros para evitar riesgo.
4. **El tiempo real.**
   1. Pulsa **▶ Simular caminata**.
   2. Abre **otra pestaña** en `localhost:8000`. Cada pestaña cuenta como una persona distinta.
   3. En la pestaña nueva, arrastra el marcador azul **"Tú"** a un punto de la ruta verde que esté más adelante y pulsa **⚠ Reportar → Actividad sospechosa**.
   4. En la primera pestaña aparece "🔁 Ruta recalculada" y la caminata sigue por el nuevo camino.
5. **La confianza.**
   - Haz clic en el reporte, pulsa *"Yo también lo veo"* desde otras pestañas (con su "Tú" a menos de 150 m) y verás cómo pasa a ✅ verificado.
   - Intenta confirmar tu propio reporte: no te deja.
   - Haz clic derecho lejos de "Tú" e intenta reportar: el sistema lo rechaza porque estás a más de 150 m.

Para empezar de nuevo, abre *¿Cómo se calcula la ruta segura?* y pulsa *Borrar todos los reportes (demo)*.

---

## Usar datos reales de delitos (FGJ-CDMX)

1. Entra a <https://datos.cdmx.gob.mx> y descarga el CSV de **"Víctimas en carpetas de investigación FGJ"** (o *Carpetas de investigación FGJ*).
2. Importa el archivo:
   ```bash
   python scripts/importar_fgj.py ruta/al/archivo.csv --desde 2023
   python scripts/importar_fgj.py ruta/al/archivo.csv --desde 2023 --sin-simulados   # para quitar los simulados
   ```
   Solo se guardan los delitos contra peatones (robo a transeúnte, lesiones, acoso, etc.) que caen dentro del área del mapa.
3. Reinicia `iniciar.py` para que se recalcule el riesgo.

---

## Probarlo desde tu celular (GPS)

Los navegadores solo dan el GPS en `localhost` o con **HTTPS**. Para abrirlo desde tu celular en la misma red Wi-Fi:

```bash
# instala mkcert: https://github.com/FiloSottile/mkcert
mkcert -install
mkcert localhost 192.168.1.50          # usa la IP de tu computadora
python iniciar.py --red --cert localhost+1.pem --key localhost+1-key.pem
```

Abre `https://192.168.1.50:8000` en el celular. Para que el celular confíe en el certificado tienes que instalarle la CA de mkcert; si no, usa un túnel como `cloudflared tunnel --url http://localhost:8000`.

Sin GPS también funciona: arrastra el marcador **"Tú"** para simular tu posición.

---

## Cómo funciona

```
navegador (Leaflet) ──REST/JSON──▶ FastAPI ──▶ NetworkX (rutas sobre el grafo de OSM)
        ▲                            │
        └──── SSE (eventos en vivo) ─┤──▶ SQLite (histórico + reportes)
```

**Riesgo de cada calle (0 a 1), según la hora:**

```
riesgo = 0.55 · histórico(calle, hora)      incidentes a < 60 m, suavizados ±2 h
       + 0.20 · estructura(calle, hora)      andadores, callejones y calles de servicio pesan más de noche
       + 0.60 · Σ reportes · e^(−t/τ)         cada reporte pierde fuerza con el tiempo
```

**Costo de cada calle para la ruta segura:** `longitud × (1 + α · riesgo)`.

### Reportes

| Tipo | Efecto | Vida media τ |
|---|---|---|
| 💡 Falta de alumbrado | +riesgo | 3 días |
| 👀 Actividad sospechosa | ++riesgo | 45 min |
| 🚓 Patrulla presente | −riesgo | 30 min |
| 🚧 Calle cerrada | penaliza; si está **verificada**, la calle se excluye | 2 h |

- Un reporte expira a las 3τ.
- Un reporte **sin verificar pesa la mitad**. Con 2 confirmaciones de otras personas queda verificado.
- Solo puedes reportar o confirmar a **menos de 150 m** de tu posición.
- Hay un **límite de 3 reportes cada 10 minutos** por sesión.

Todos los parámetros están en `backend/rusac/config.py`.

### Privacidad (como en la política de la empresa)

- **No se guarda la ubicación ni las rutas** de quien usa la app. Tu posición solo se usa para validar el reporte en ese momento.
- De cada reporte se guarda el punto del incidente y un hash de la sesión, con una sal aleatoria que cambia cada vez que arranca el servidor.
- El servidor no escribe registros de acceso.

---

## Estructura

```
iniciar.py                  arranque con un solo comando
backend/rusac/
  config.py                 parámetros del modelo y de los reportes
  graph.py                  grafo peatonal y geometría
  risk.py                   modelo de riesgo
  routing.py                ruta corta contra ruta segura
  store.py                  SQLite
  app.py                    API (FastAPI) y eventos en vivo (SSE)
  osm_export.py             OSMnx → data/grafo.json
  demo.py                   mapa sintético e incidentes simulados
scripts/
  descargar_mapa.py         OpenStreetMap → data/grafo.json
  importar_fgj.py           CSV de la FGJ → histórico real
frontend/                   index.html, app.js, styles.css, Leaflet incluido
tests/test_mvp.py           pruebas (pytest)
```

### API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/meta` | Zona, trayectos predefinidos, categorías |
| GET | `/api/calles` | Geometría de las calles |
| GET | `/api/riesgo?hora=21` | Riesgo por calle (semáforo) |
| POST | `/api/ruta` | `{origen, destino, hora, alfa}` → ruta corta, ruta segura y comparación |
| GET/POST | `/api/reportes` | Listar o crear reportes (encabezado `X-Sesion`) |
| POST | `/api/reportes/{id}/confirmar` | Confirmar un reporte |
| GET | `/api/eventos` | Eventos en vivo (Server-Sent Events) |

La documentación interactiva está en `http://localhost:8000/docs`.

### Pruebas

```bash
pip install -r requirements-dev.txt
pytest
```

---

## Limitaciones conocidas y siguientes pasos

- **Datos sin verificar.** Las coordenadas de ESCOM y de las estaciones se buscan en OSM; si no se encuentran, se usan coordenadas aproximadas de `config.py`, y la consola indica cuál se usó. Revisa los pesos del modelo con datos reales.
- **Mapa base.** Usa los mosaicos de OpenStreetMap, que necesitan internet. Si no hay conexión, la página dibuja las calles desde los datos locales.
- **Reportes de prueba.** Los reportes son de demostración: no hay cuentas ni verificación con correo institucional.
- **Rendimiento.** Con muchos incidentes (más de 5,000) el arranque tarda unos segundos porque calcula la distancia de cada incidente a cada calle.
- **Siguientes pasos:**
  - Usar PostGIS y pgRouting para crecer a otras escuelas.
  - Convertirlo en una app web instalable (PWA).
  - Agregar notificaciones antes de la hora de salida.
  - Reemplazar el término histórico por un modelo de ML.

Datos del mapa © colaboradores de OpenStreetMap (ODbL). Leaflet © sus autores (BSD-2).
