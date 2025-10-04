import os, time, math
import requests
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

NASA_API = "https://api.nasa.gov/neo/rest/v1"
NASA_KEY = os.getenv("NASA_API_KEY", "9cL9fpjqbydKR16ZMCJ1znPDTf9xN6uMOyvHcpFJ")
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))

SSOD_BASE = "https://ssp.imcce.fr/webservices/ssodnet/api"
SBDB_BASE = "https://ssd-api.jpl.nasa.gov/sbdb.api"

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
        "actions": [
            "Follow-up com observatórios (óptico/radar).",
            "Atualizar efemérides e propagar com perturbações."
        ],
        "suitable_for": ["LOW", "MODERATE", "HIGH", "CRITICAL"]
    })

    if level in ["MODERATE", "HIGH", "CRITICAL"]:
        suggestions.append({
            "title": "Coordenação internacional (IAWN/NEO coordination)",
            "when": "curto prazo",
            "rationale": "Padroniza avaliação de risco e acesso a ativos de observação/defesa planetária.",
            "actions": [
                "Compartilhar elementos orbitais atualizados e janelas de visibilidade.",
                "Planejar campanhas observacionais multi-longitude."
            ],
            "suitable_for": ["MODERATE", "HIGH", "CRITICAL"]
        })

    if short_window or (miss is not None and miss < 1_000_000):
        suggestions.append({
            "title": "Proteção civil e preparação de emergência",
            "when": "curtíssimo prazo",
            "rationale": "Mitigar danos caso ocorra entrada atmosférica inesperada.",
            "actions": [
                "Planos de evacuação/comunicação pública.",
                "Inventário de abrigos e protocolos para infraestrutura crítica."
            ],
            "suitable_for": ["HIGH", "CRITICAL"]
        })

    if dia >= 0.05 and not short_window:
        suggestions.append({
            "title": "Deflexão cinética (Kinetic Impactor)",
            "when": "médio/longo prazo",
            "rationale": "Pequena mudança de velocidade ao longo de anos pode evitar impacto.",
            "actions": [
                "Janela de lançamento e Δv.",
                "Análises de coesão/rotação do alvo."
            ],
            "suitable_for": ["MODERATE", "HIGH"]
        })

    if dia >= 0.1 and year_window:
        suggestions.append({
            "title": "Trator gravitacional",
            "when": "longo prazo",
            "rationale": "Empuxo gravitacional contínuo, útil quando há muitos anos de antecedência.",
            "actions": [
                "Estimar massa/forma do NEO; simular permanência orbital.",
                "Avaliar consumo de propelente e ressonâncias."
            ],
            "suitable_for": ["MODERATE", "HIGH"]
        })

    if (level in ["HIGH", "CRITICAL"]) and (days is not None and days <= 365) and dia >= 0.15:
        suggestions.append({
            "title": "Explosão nuclear standoff (último recurso)",
            "when": "curto prazo",
            "rationale": "Opção extrema quando o tempo é insuficiente.",
            "actions": [
                "Análise legal e coordenação internacional.",
                "Estudo de fragmentação e risco colateral."
            ],
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

# -------- Enrichment (mass/density) --------
ENRICH_TTL = int(os.getenv("ENRICH_TTL", "21600"))  # 6h
_enrich_cache: Dict[str, Tuple[float, Any]] = {}

def _enrich_cache_get(key):
    item = _enrich_cache.get(key)
    if not item: return None
    exp, data = item
    if time.time() > exp:
        _enrich_cache.pop(key, None)
        return None
    return data

def _enrich_cache_set(key, data, ttl=ENRICH_TTL):
    _enrich_cache[key] = (time.time() + ttl, data)

def _num(x):
    try:
        if isinstance(x, dict):
            x = x.get("value")
        if x is None: return None
        return float(str(x).strip())
    except Exception:
        return None

def ssod_quaero(query: str):
    try:
        r = requests.get(f"{SSOD_BASE}/quaero/", params={"q": query}, timeout=30)
        if r.status_code != 200:
            return None
        js = r.json()
        if isinstance(js, list) and js:
            return js[0].get("id") or js[0].get("spkid") or js[0].get("name")
        return None
    except Exception:
        return None

def ssod_card(ssod_id: str):
    try:
        r = requests.get(f"{SSOD_BASE}/ssocard/{ssod_id}", timeout=30)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

def sbdb_phys(sstr: str):
    try:
        r = requests.get(SBDB_BASE, params={"sstr": sstr, "phys-par": "1"}, timeout=30)
        if r.status_code != 200:
            return None
        js = r.json()
        return js.get("phys_par", {}) if isinstance(js, dict) else None
    except Exception:
        return None

TAXO_RHO = {
    "C": 1.3, "B": 1.3, "G": 1.3, "F": 1.3,
    "S": 2.7, "Q": 2.7, "V": 3.0, "A": 3.0, "R": 3.0, "K": 2.5, "L": 2.5,
    "M": 5.3, "X": 3.5, "E": 3.0, "P": 1.8, "D": 1.5, "T": 1.6,
}

def estimate_mass_from_diameter_density(d_km: Optional[float], rho_g_cm3: Optional[float]):
    if not d_km or not rho_g_cm3:
        return None
    r_m = (d_km * 1000.0) / 2.0
    rho = rho_g_cm3 * 1000.0  # g/cm^3 -> kg/m^3
    vol = (4.0/3.0) * math.pi * (r_m ** 3)
    return rho * vol  # kg

def extract_taxonomy(card: dict):
    if not card: return None
    tx = None
    p = card.get("parameters") if isinstance(card, dict) else None
    if isinstance(p, dict):
        taxo = p.get("taxonomy") or p.get("taxon") or {}
        if isinstance(taxo, dict):
            tx = taxo.get("class") or taxo.get("type") or taxo.get("complex")
    return tx

def extract_phys_from_ssocard(card: dict):
    if not card: return {}
    p = card.get("parameters") or {}
    phys = p.get("physical") or {}
    diameter = _num(phys.get("diameter") or phys.get("equivalent_diameter"))
    mass = _num(phys.get("mass") or phys.get("GM"))
    density = _num(phys.get("density") or phys.get("rho"))
    taxo = extract_taxonomy(card)
    refs = card.get("references") or []
    bib = None
    if isinstance(refs, list) and refs:
        for ref in refs:
            if isinstance(ref, dict) and ref.get("bibcode"):
                bib = ref["bibcode"]; break
    return {"diameter_km": diameter, "mass_kg": mass, "density_g_cm3": density, "taxonomy": taxo, "bibcode": bib}

def extract_phys_from_sbdb(phys: dict):
    if not phys: return {}
    diameter = _num(phys.get("diameter"))
    mass = _num(phys.get("mass") or phys.get("GM"))
    density = _num(phys.get("density") or phys.get("rho"))
    return {"diameter_km": diameter, "mass_kg": mass, "density_g_cm3": density}

def enrich_by_label(label: str):
    ck = ("enrich", label)
    cached = _enrich_cache_get(ck)
    if cached is not None:
        return cached

    result = {"source": None, "mass_kg": None, "density_g_cm3": None, "diameter_km": None, "taxonomy": None, "bibcode": None, "note": None}
    sid = ssod_quaero(label)
    if sid:
        card = ssod_card(sid)
        ssop = extract_phys_from_ssocard(card)
        if any([ssop.get("mass_kg"), ssop.get("density_g_cm3"), ssop.get("diameter_km")]):
            result.update(ssop)
            result["source"] = "ssodnet"
    if result["mass_kg"] is None or result["density_g_cm3"] is None or result["diameter_km"] is None:
        sbp = extract_phys_from_sbdb(sbdb_phys(label))
        for k, v in sbp.items():
            if result.get(k) is None and v is not None:
                result[k] = v
        if any([sbp.get("mass_kg"), sbp.get("density_g_cm3"), sbp.get("diameter_km")]) and not result["source"]:
            result["source"] = "sbdb"
    if result["mass_kg"] is None:
        rho = result["density_g_cm3"]
        if rho is None:
            rho = TAXO_RHO.get((result.get("taxonomy") or "").upper())
        est = estimate_mass_from_diameter_density(result.get("diameter_km"), rho)
        if est is not None:
            result["mass_kg"] = est
            result["note"] = "estimated from diameter and density"
            if result["source"] is None:
                result["source"] = "estimate"

    _enrich_cache_set(ck, result)
    return result

app = FastAPI(title="NeoWS Python Proxy — Advanced", version="2.0.0", docs_url="/")

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
def neo_detail(
    neo_id: str,
    mitigations: bool = Query(True, description="Inclui avaliação por padrão"),
    enrich: bool = Query(False, description="Se true, junta massa/densidade")
):
    params = {"api_key": NASA_KEY}
    neo = _get(f"{NASA_API}/neo/{neo_id}", params)
    if mitigations:
        neo["assessment"] = build_assessment(neo)
    if enrich:
        label = neo.get("name") or neo.get("designation") or str(neo_id)
        neo["enrichment"] = enrich_by_label(label)
    return neo

@app.get("/neo/browse")
def neo_browse(page: int = 0, size: int = 20, mitigations: bool = Query(False), enrich: bool = Query(False)):
    params = {"api_key": NASA_KEY, "page": page, "size": size}
    data = _get(f"{NASA_API}/neo/browse", params)
    if mitigations or enrich:
        for neo in data.get("near_earth_objects", []):
            if mitigations:
                neo["assessment"] = build_assessment(neo)
            if enrich:
                label = neo.get("name") or neo.get("designation") or str(neo.get("id"))
                neo["enrichment"] = enrich_by_label(label)
    return data

def _any_approach_in_window(neo: dict, date_from: Optional[str], date_to: Optional[str], body: Optional[str]) -> bool:
    if not date_from and not date_to and not body:
        return True
    df = _parse_iso(date_from).date() if date_from else None
    dt = _parse_iso(date_to).date() if date_to else None
    bnorm = body.lower() if body else None
    for ap in neo.get("close_approach_data", []):
        try:
            dstr = ap.get("close_approach_date_full") or ap.get("close_approach_date")
            d = _parse_iso(dstr).date() if dstr else None
            orbiting = ap.get("orbiting_body", "")
            if df and d and d < df: 
                continue
            if dt and d and d > dt: 
                continue
            if bnorm and orbiting.lower() != bnorm: 
                continue
            return True
        except Exception:
            continue
    return False

def _passes_filters(neo: dict,
                    hazardous: Optional[bool],
                    min_diameter_km: Optional[float],
                    max_diameter_km: Optional[float],
                    min_miss_km: Optional[float],
                    max_miss_km: Optional[float],
                    min_rel_vel_kms: Optional[float],
                    max_rel_vel_kms: Optional[float],
                    days_min: Optional[int],
                    days_max: Optional[int],
                    mag_h_min: Optional[float],
                    mag_h_max: Optional[float],
                    approach_body: Optional[str],
                    date_from: Optional[str],
                    date_to: Optional[str]) -> bool:
    m = compute_metrics(neo)
    if hazardous is not None and m["is_hazardous"] != hazardous:
        return False
    if min_diameter_km is not None and (m["diameter_km"] is None or m["diameter_km"] < min_diameter_km):
        return False
    if max_diameter_km is not None and (m["diameter_km"] is None or m["diameter_km"] > max_diameter_km):
        return False
    if min_miss_km is not None and (m["min_miss_km"] is None or m["min_miss_km"] < min_miss_km):
        return False
    if max_miss_km is not None and (m["min_miss_km"] is None or m["min_miss_km"] > max_miss_km):
        return False
    if min_rel_vel_kms is not None and m["max_rel_vel_kms"] < min_rel_vel_kms:
        return False
    if max_rel_vel_kms is not None and m["max_rel_vel_kms"] > max_rel_vel_kms:
        return False
    if days_min is not None and (m["days_to_soonest_approach"] is None or m["days_to_soonest_approach"] < days_min):
        return False
    if days_max is not None and (m["days_to_soonest_approach"] is None or m["days_to_soonest_approach"] > days_max):
        return False
    H = m["absolute_magnitude_h"]
    if mag_h_min is not None and (H is None or H < mag_h_min):
        return False
    if mag_h_max is not None and (H is None or H > mag_h_max):
        return False
    if not _any_approach_in_window(neo, date_from, date_to, approach_body):
        return False
    return True

@app.get("/neo/filter")
def neo_filter(
    pages: int = Query(3, ge=1, le=50),
    size: int = Query(50, ge=1, le=100),
    limit: int = Query(200, ge=1, le=1000),
    hazardous: Optional[bool] = None,
    min_diameter_km: Optional[float] = None,
    max_diameter_km: Optional[float] = None,
    min_miss_km: Optional[float] = None,
    max_miss_km: Optional[float] = None,
    min_rel_vel_kms: Optional[float] = None,
    max_rel_vel_kms: Optional[float] = None,
    days_min: Optional[int] = None,
    days_max: Optional[int] = None,
    mag_h_min: Optional[float] = None,
    mag_h_max: Optional[float] = None,
    approach_body: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    mitigations: bool = Query(True),
    enrich: bool = Query(False)
):
    results: List[dict] = []
    seen_ids = set()
    for page in range(pages):
        data = _get(f"{NASA_API}/neo/browse", {"api_key": NASA_KEY, "page": page, "size": size})
        for neo in data.get("near_earth_objects", []):
            nid = neo.get("id")
            if nid in seen_ids: continue
            if _passes_filters(neo, hazardous, min_diameter_km, max_diameter_km,
                               min_miss_km, max_miss_km, min_rel_vel_kms, max_rel_vel_kms,
                               days_min, days_max, mag_h_min, mag_h_max, approach_body,
                               date_from, date_to):
                if mitigations:
                    neo["assessment"] = build_assessment(neo)
                if enrich:
                    label = neo.get("name") or neo.get("designation") or str(nid)
                    neo["enrichment"] = enrich_by_label(label)
                results.append(neo)
                seen_ids.add(nid)
                if len(results) >= limit:
                    return {"count": len(results), "near_earth_objects": results}
    return {"count": len(results), "near_earth_objects": results}

@app.get("/neo/hazardous")
def neo_hazardous(
    page: int = 0,
    size: int = 50,
    min_diameter_km: float = 0.0,
    max_miss_distance_km: float = 10_000_000.0,
    mitigations: bool = Query(True),
    max_diameter_km: Optional[float] = None,
    min_miss_distance_km: Optional[float] = None,
    min_rel_vel_kms: Optional[float] = None,
    max_rel_vel_kms: Optional[float] = None,
    days_max: Optional[int] = None,
    approach_body: Optional[str] = None,
    enrich: bool = Query(False)
):
    raw = neo_browse(page=page, size=size, mitigations=False, enrich=False)
    filtered = []
    for neo in raw.get("near_earth_objects", []):
        try:
            metrics = compute_metrics(neo)
            if not neo.get("is_potentially_hazardous_asteroid", False):
                continue
            if metrics["diameter_km"] is None or metrics["diameter_km"] < min_diameter_km:
                continue
            if max_diameter_km is not None and metrics["diameter_km"] > max_diameter_km:
                continue
            mm = metrics["min_miss_km"]
            if mm is None: 
                continue
            if mm > max_miss_distance_km:
                continue
            if min_miss_distance_km is not None and mm < min_miss_distance_km:
                continue
            if min_rel_vel_kms is not None and metrics["max_rel_vel_kms"] < min_rel_vel_kms:
                continue
            if max_rel_vel_kms is not None and metrics["max_rel_vel_kms"] > max_rel_vel_kms:
                continue
            if days_max is not None and (metrics["days_to_soonest_approach"] is None or metrics["days_to_soonest_approach"] > days_max):
                continue
            if approach_body and not _any_approach_in_window(neo, None, None, approach_body):
                continue
            if mitigations:
                neo["assessment"] = build_assessment(neo)
            if enrich:
                label = neo.get("name") or neo.get("designation") or str(neo.get("id"))
                neo["enrichment"] = enrich_by_label(label)
            filtered.append(neo)
        except Exception:
            continue
    return {"count": len(filtered), "near_earth_objects": filtered}

@app.get("/neo/enrich/{neo_id}")
def neo_enrich(neo_id: str):
    neo = _get(f"{NASA_API}/neo/{neo_id}", {"api_key": NASA_KEY})
    label = neo.get("name") or neo.get("designation") or str(neo_id)
    data = enrich_by_label(label)
    return {"neo_id": neo_id, "label": label, "enrichment": data}
