# NeoWS Python Proxy + Mitigations (FastAPI)

Proxy para NASA NeoWS com CORS, cache e **avaliação + mitigações** (heurística educacional).

## Endpoints
- `GET /health`
- `GET /neo/feed?start_date=YYYY-MM-DD[&end_date=YYYY-MM-DD][&mitigations=true]`
- `GET /neo/{neo_id}[?mitigations=true]`
- `GET /neo/browse?page=0&size=20[&mitigations=true]`
- `GET /neo/hazardous?page=0&size=50&min_diameter_km=0.0&max_miss_distance_km=10000000[&mitigations=true]`
- `GET /neo/assess/{neo_id}`

## Deploy (Render)
- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Env:
  - `NASA_API_KEY` (https://api.nasa.gov)
  - `CORS_ORIGINS` (ex.: https://SEUusuario.github.io)
  - `CACHE_TTL` (opcional)

## Dev local
```
pip install -r requirements.txt
uvicorn main:app --reload
```
