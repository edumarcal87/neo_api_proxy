import os, time, math
import requests
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

NASA_API = "https://api.nasa.gov/neo/rest/v1"
NASA_KEY = os.getenv("NASA_API_KEY", "DEMO_KEY")
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))

_cache: Dict[Any, Tuple[float, Any]] = {}

def _cache_get(key):
    item = _cache.get(key)
    if not item: return None
    exp, data = item
    if time.time() > exp:
        _cache.pop(key, None)
        return None
    return data

def _cache_set(key, data, ttl=CACHE_TTL):
    _cache[key] = (time.time() + ttl, data)

def _get(url, params):
    cache_key = (url, tuple(sorted(params.items())))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    r = requests.get(url, params=params, timeout=30)
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    data = r.json()
    _cache_set(cache_key, data)
    return data

def _parse_iso(dt: str) -> datetime:
    try:
        return datetime.fromisoformat(dt.replace("Z", "+00:00"))
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(dt, fmt).replace(tzinfo=timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)

def compute_metrics(neo: dict) -> dict:
    diameter_km = None
    try:
        diameter_km = neo["estimated_diameter"]["kilometers"]["estimated_diameter_max"]
    except Exception:
        diameter_km = None

    is_hazardous = bool(neo.get("is_potentially_hazardous_asteroid", False))
    H = neo.get("absolute_magnitude_h", None)

    min_miss = float("inf")
    soonest_dt = None
    max_rel_vel_kms = 0.0

    for ap in neo.get("close_approach_data", []):
        try:
            md_km = float(ap["miss_distance"]["kilometers"])
            rv_kms = float(ap["relative_velocity"]["kilometers_per_second"])
            dt_str = ap.get("close_approach_date_full") or ap.get("close_approach_date")
            dt = _parse_iso(dt_str) if dt_str else None

            if md_km < min_miss:
                min_miss = md_km
            if rv_kms > max_rel_vel_kms:
                max_rel_vel_kms = rv_kms
            if dt and (soonest_dt is None or dt < soonest_dt):
                soonest_dt = dt
        except Exception:
            continue

    if min_miss == float("inf"):
        min_miss = None

    now = datetime.now(timezone.utc)
    days_to_soonest = None
    if soonest_dt:
        delta = soonest_dt - now
        days_to_soonest = int(delta.total_seconds() // 86400)

    return {
        "diameter_km": diameter_km,
        "is_hazardous": is_hazardous,
        "min_miss_km": min_miss,
        "days_to_soonest_approach": days_to_soonest,
        "soonest_approach_utc": soonest_dt.isoformat() if soonest_dt else None,
        "max_rel_vel_kms": max_rel_vel_kms,
        "absolute_magnitude_h": H,
    }

def classify_threat(m: dict) -> str:
    dia = m.get("diameter_km") or 0.0
    miss = m.get("min_miss_km")
    days = m.get("days_to_soonest_approach")

    near = (miss is not None and miss < 1_000_000)
    mid_near = (miss is not None and miss < 5_000_000)
    soon = (days is not None and days <= 30)
    mid_term = (days is not None and 30 < days <= 365)

    if dia >= 0.3: base = 3
    elif dia >= 0.05: base = 2
    else: base = 1

    lvl = base
    if near: lvl += 1
    elif mid_near: lvl += 0.5
    if soon: lvl += 1
    elif mid_term: lvl += 0.5

    if lvl >= 4: return "CRITICAL"
    if lvl >= 3: return "HIGH"
    if lvl >= 2: return "MODERATE"
    return "LOW"

def mitigation_suggestions(m: dict, level: str) -> List[dict]:
    dia = m.get("diameter_km") or 0.0
    days = m.get("days_to_soonest_approach")
    miss = m.get("min_miss_km")
    vel = m.get("max_rel_vel_kms") or 0.0

    short_window = (days is not None and days <= 30)
    year_window = (days is not None and days > 365)

    suggestions: List[dict] = []

    suggestions.append({
        "title": "Monitoramento reforçado e refinamento orbital",
        "when": "imediato e contínuo",
        "rationale": "Melhora a precisão da órbita e reduz incertezas antes de qualquer decisão.",
        "actions": ["Follow-up com observatórios (óptico/radar).", "Atualizar efemérides e propagar com perturbações."],
        "suitable_for": ["LOW", "MODERATE", "HIGH", "CRITICAL"]
    })

    if level in ["MODERATE", "HIGH", "CRITICAL"]:
        suggestions.append({
            "title": "Coordenação internacional (IAWN/NEO coordination)",
            "when": "curto prazo",
            "rationale": "Padroniza avaliação de risco e acesso a ativos de observação/defesa planetária.",
            "actions": ["Compartilhar elementos orbitais e janelas de visibilidade.", "Planejar campanhas observacionais multi-longitude."],
            "suitable_for": ["MODERATE", "HIGH", "CRITICAL"]
        })

    if short_window or (miss is not None and miss < 1_000_000):
        suggestions.append({
            "title": "Proteção civil e preparação de emergência",
            "when": "curtíssimo prazo",
            "rationale": "Mitigar danos caso ocorra entrada atmosférica inesperada.",
            "actions": ["Planos de evacuação/comunicação pública.", "Inventário de abrigos e protocolos para infraestrutura crítica."],
            "suitable_for": ["HIGH", "CRITICAL"]
        })

    if dia >= 0.05 and not short_window:
        suggestions.append({
            "title": "Deflexão cinética (Kinetic Impactor)",
            "when": "médio/longo prazo",
            "rationale": "Pequena mudança de velocidade ao longo de anos pode evitar impacto.",
            "actions": ["Janela de lançamento e Δv.", "Análises de coesão/rotação do alvo."],
            "suitable_for": ["MODERATE", "HIGH"]
        })

    if dia >= 0.1 and year_window:
        suggestions.append({
            "title": "Trator gravitacional",
            "when": "longo prazo",
            "rationale": "Empuxo gravitacional contínuo, útil quando há muitos anos de antecedência.",
            "actions": ["Estimar massa/forma do NEO; simular permanência orbital.", "Avaliar consumo de propelente e ressonâncias."],
            "suitable_for": ["MODERATE", "HIGH"]
        })

    if (level in ["HIGH", "CRITICAL"]) and (days is not None and days <= 365) and dia >= 0.15:
        suggestions.append({
            "title": "Explosão nuclear standoff (último recurso)",
            "when": "curto prazo",
            "rationale": "Opção extrema quando o tempo é insuficiente.",
            "actions": ["Análise legal e coordenação internacional.", "Estudo de fragmentação e risco colateral."],
            "suitable_for": ["HIGH", "CRITICAL"]
        })

    return suggestions

def build_assessment(neo: dict) -> dict:
    metrics = compute_metrics(neo)
    level = classify_threat(metrics)
    mitigations = mitigation_suggestions(metrics, level)
    return {
        "metrics": metrics,
        "threat_level": level,
        "mitigations": mitigations,
        "disclaimer": "Avaliação heurística educacional; não substitui avaliações oficiais."
    }

from fastapi import FastAPI
app = FastAPI(title="NeoWS Python Proxy + Mitigations", version="1.1.0", docs_url="/")

from fastapi.middleware.cors import CORSMiddleware
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
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    mitigations: bool = Query(False, description="Se true, inclui avaliação/mitigações por NEO")
):
    params = {"api_key": NASA_KEY, "start_date": start_date}
    if end_date: params["end_date"] = end_date
    data = _get(f"{NASA_API}/feed", params)

    if mitigations:
        neos_by_day = data.get("near_earth_objects", {})
        for day, neos in neos_by_day.items():
            for neo in neos:
                neo["assessment"] = build_assessment(neo)
    return data

@app.get("/neo/{neo_id}")
def neo_detail(neo_id: str, mitigations: bool = Query(True, description="Inclui avaliação por padrão")):
    params = {"api_key": NASA_KEY}
    neo = _get(f"{NASA_API}/neo/{neo_id}", params)
    if mitigations:
        neo["assessment"] = build_assessment(neo)
    return neo

@app.get("/neo/browse")
def neo_browse(page: int = 0, size: int = 20, mitigations: bool = Query(False)):
    params = {"api_key": NASA_KEY, "page": page, "size": size}
    data = _get(f"{NASA_API}/neo/browse", params)
    if mitigations:
        for neo in data.get("near_earth_objects", []):
            neo["assessment"] = build_assessment(neo)
    return data

@app.get("/neo/hazardous")
def neo_hazardous(
    page: int = 0,
    size: int = 50,
    min_diameter_km: float = 0.0,
    max_miss_distance_km: float = 10_000_000.0,
    mitigations: bool = Query(True)
):
    raw = neo_browse(page=page, size=size, mitigations=False)
    filtered = []
    for neo in raw.get("near_earth_objects", []):
        try:
            haz = neo.get("is_potentially_hazardous_asteroid", False)
            dia = neo["estimated_diameter"]["kilometers"]["estimated_diameter_max"]
            miss_list = [float(ap["miss_distance"]["kilometers"]) for ap in neo.get("close_approach_data", [])]
            min_miss = min(miss_list) if miss_list else float("inf")
        except Exception:
            continue
        if haz and dia >= min_diameter_km and min_miss <= max_miss_distance_km:
            if mitigations:
                neo["assessment"] = build_assessment(neo)
            filtered.append(neo)
    return {"count": len(filtered), "near_earth_objects": filtered}

@app.get("/neo/assess/{neo_id}")
def neo_assess(neo_id: str):
    params = {"api_key": NASA_KEY}
    neo = _get(f"{NASA_API}/neo/{neo_id}", params)
    return build_assessment(neo)
