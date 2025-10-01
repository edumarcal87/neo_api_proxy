import os, time
import requests
from typing import Optional, Tuple, Any, Dict
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

NASA_API = "https://api.nasa.gov/neo/rest/v1"
NASA_KEY = os.getenv("NASA_API_KEY", "9cL9fpjqbydKR16ZMCJ1znPDTf9xN6uMOyvHcpFJ")  # defina no deploy
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")  # ex: "https://seuusuario.github.io"
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))  # segundos

_cache: Dict[Any, Tuple[float, Any]] = {}  # chave -> (expira_em_ts, dados)

def _cache_get(key):
    item = _cache.get(key)
    if not item:
        return None
    expira, dados = item
    if time.time() > expira:
        _cache.pop(key, None)
        return None
    return dados

def _cache_set(key, dados, ttl=CACHE_TTL):
    _cache[key] = (time.time() + ttl, dados)

def _get(url, params):
    key = (url, tuple(sorted(params.items())))
    cached = _cache_get(key)
    if cached is not None:
        return cached
    r = requests.get(url, params=params, timeout=30)
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    data = r.json()
    _cache_set(key, data)
    return data

app = FastAPI(title="NeoWS Python Proxy", version="1.0.0", docs_url="/")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/neo/feed")
def neo_feed(
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD")
):
    params = {"api_key": NASA_KEY, "start_date": start_date}
    if end_date:
        params["end_date"] = end_date
    return _get(f"{NASA_API}/feed", params)

@app.get("/neo/{neo_id}")
def neo_detail(neo_id: str):
    params = {"api_key": NASA_KEY}
    return _get(f"{NASA_API}/neo/{neo_id}", params)

@app.get("/neo/browse")
def neo_browse(page: int = 0, size: int = 20):
    params = {"api_key": NASA_KEY, "page": page, "size": size}
    return _get(f"{NASA_API}/neo/browse", params)
