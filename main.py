import os, time, math, re, requests, math

from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any, List
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

NASA_API = "https://api.nasa.gov/neo/rest/v1"
NASA_KEY = os.getenv("NASA_API_KEY", "9cL9fpjqbydKR16ZMCJ1znPDTf9xN6uMOyvHcpFJ")
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))
DEFAULT_RHO_G_CM3 = float(os.getenv("DEFAULT_RHO_G_CM3", "2.5"))
DEFAULT_ALBEDO    = float(os.getenv("DEFAULT_ALBEDO", "0.14"))
SSOD_BASE = "https://ssp.imcce.fr/webservices/ssodnet/api"
SBDB_BASE = "https://ssd-api.jpl.nasa.gov/sbdb.api"

# Alvos básicos
TARGET_RHO = {
    "rock": 2700.0,        # kg/m^3 (rocha cristalina ~2.7)
    "sedimentary": 2200.0, # kg/m^3
    "crystalline": 2700.0, # kg/m^3
    "water": 1000.0,       # kg/m^3
    "ice": 930.0           # kg/m^3 (gelo ~0.93)
}

G_EARTH = 9.80665  # m/s^2
JOULES_PER_KT_TNT = 4.184e12  # 1 kt TNT
JOULES_PER_MT_TNT = 4.184e15  # 1 Mt TNT

# --- ocean/tsunami defaults ---
OCEAN_DEFAULT_DEPTH_M = 4000.0     # profundidade típica oceano aberto
COAST_DEFAULT_DEPTH_M = 50.0       # profundidade média próxima à costa
NEARFIELD_RADIUS_FACTOR = 2.0      # r0 ~ 2 × (D_tc/2)
INITIAL_WAVE_ALPHA = 0.10          # A0 ~ 0.10 × D_tc (heurística)
DISPERSION_LENGTH_KM = 1000.0      # escala de atenuação por dispersão (km)
RUNUP_FACTOR = 2.0                 # run-up ~ 2 × amplitude costeira (bem 1ª ordem)

# --- sísmico ---
SEISMIC_COUPLING_DEFAULT = 1e-4    # fração da energia cinética que vira energia sísmica

def _parse_csv_floats(s: str | None) -> list[float]:
    if not s: return []
    out = []
    for tok in s.split(","):
        try:
            out.append(float(tok.strip()))
        except Exception:
            pass
    return out

def _ocean_wavefield_from_crater(
    Dtc_m: float | None,
    water_depth_m: float,
    coast_depth_m: float,
    distances_km: list[float],
    nearfield_radius_factor: float = NEARFIELD_RADIUS_FACTOR,
    alpha_init: float = INITIAL_WAVE_ALPHA,
    Ld_km: float = DISPERSION_LENGTH_KM,
    runup_factor: float = RUNUP_FACTOR,
):
    """
    Estimativa 1ª ordem de tsunami por impacto:
    - A0 ≈ alpha_init × D_tc
    - r0 ≈ nearfield_radius_factor × (D_tc/2)
    - A_deep(R) ≈ A0 × (r0/R) × exp(-R/Ld)             (espalhamento + dispersão)
    - A_coast ≈ A_deep × ( (h_deep / h_coast) ** 0.25 ) (shoaling ~ Green's law)
    - Run-up ≈ runup_factor × A_coast
    """
    if not Dtc_m or Dtc_m <= 0:
        return None

    r0_m = nearfield_radius_factor * (Dtc_m / 2.0)
    A0_m = alpha_init * Dtc_m
    results = []
    for R_km in (distances_km or [50.0, 100.0, 200.0, 500.0]):
        R = max(R_km * 1000.0, r0_m)
        A_deep = A0_m * (r0_m / R) * math.exp(- (R_km / max(Ld_km, 1.0)))
        shoal = (water_depth_m / max(coast_depth_m, 1.0)) ** 0.25
        A_coast = A_deep * shoal
        runup = runup_factor * A_coast
        results.append({
            "distance_km": R_km,
            "deep_water_amp_m": A_deep,
            "coastal_amp_m": A_coast,
            "runup_m": runup
        })
    return {
        "initial_amp_m": A0_m,
        "nearfield_radius_km": r0_m / 1000.0,
        "far_field": results
    }

def _seismic_from_energy(E_k_joules: float | None, coupling: float = SEISMIC_COUPLING_DEFAULT):
    """
    Estima magnitude momento M_w a partir da energia sísmica:
    log10(E_s [J]) ≈ 4.8 + 1.5 M_w  →  M_w = (log10(E_s) - 4.8) / 1.5
    com E_s = coupling × E_kinetic. (Relação clássica Gutenberg–Richter/Kanamori; 1ª ordem.)
    """
    if not E_k_joules or E_k_joules <= 0:
        return {"Mw": None, "E_s_j": None, "coupling": coupling}
    E_s = coupling * E_k_joules
    Mw = (math.log10(E_s) - 4.8) / 1.5
    return {"Mw": Mw, "E_s_j": E_s, "coupling": coupling}

def _to_kg_m3_from_g_cm3(x):
    v = _num(x)
    return None if v is None else v * 1000.0

def _resolve_velocity_kms(neo: dict, velocity_kms: float | None):
    # usa o que veio da query; se não, tenta "metrics"; senão, 20 km/s (típico)
    if velocity_kms:
        return float(velocity_kms)
    try:
        m = compute_metrics(neo) or {}
        v = m.get("max_rel_vel_kms") or m.get("min_rel_vel_kms")
        if v:
            return float(v)
    except Exception:
        pass
    return 20.0  # típico de impactos na Terra (11–72 km/s). :contentReference[oaicite:3]{index=3}

def _resolve_diameter_km(neo: dict, enr: dict | None, override_km: float | None):
    if override_km is not None:
        return float(override_km)
    if enr and enr.get("diameter_km"):
        return float(enr["diameter_km"])
    # fallback: NeoWS média min/max
    d_ctx = _diameter_from_neows(neo)
    if d_ctx is not None:
        return d_ctx
    # último recurso: por H com albedo padrão
    return estimate_diameter_from_H(neo.get("absolute_magnitude_h"), DEFAULT_ALBEDO)

def _resolve_density_kg_m3(enr: dict | None, override_g_cm3: float | None):
    if override_g_cm3 is not None:
        return _to_kg_m3_from_g_cm3(override_g_cm3)
    if enr and enr.get("density_g_cm3") is not None:
        return _to_kg_m3_from_g_cm3(enr["density_g_cm3"])
    return _to_kg_m3_from_g_cm3(DEFAULT_RHO_G_CM3)

def _resolve_mass_kg(enr: dict | None, override_mass_kg: float | None, d_km: float | None, rho_g_cm3: float | None):
    if override_mass_kg is not None:
        return float(override_mass_kg)
    if enr and enr.get("mass_kg") is not None:
        return float(enr["mass_kg"])
    # estima pela esfera
    return estimate_mass_from_diameter_density(d_km, rho_g_cm3)

def _crater_transient_diameter_m(L_m, v_ms, rho_i, rho_t, theta_deg):
    """
    Lei de pi-scaling (EIEP Eq. 21*):
    D_tc = 1.161 * (rho_i/rho_t)^(1/3) * L^0.78 * v^0.44 * g^-0.22 * (sin(theta))^(1/3)
    - L em m, v em m/s, g em m/s^2 → D_tc em m.  :contentReference[oaicite:4]{index=4}
    """
    if not all([L_m, v_ms, rho_i, rho_t, theta_deg]):
        return None
    try:
        mu = (rho_i / rho_t) ** (1.0 / 3.0)
        sin_term = math.sin(math.radians(theta_deg)) ** (1.0 / 3.0)
        return 1.161 * mu * (L_m ** 0.78) * (v_ms ** 0.44) * (G_EARTH ** -0.22) * sin_term
    except Exception:
        return None

def _crater_final_from_transient_km(Dtc_m):
    """ D_fr ≈ 1.25 * D_tc (simples). Resultado em km.  :contentReference[oaicite:5]{index=5} """
    if Dtc_m is None:
        return None
    return 1.25 * (Dtc_m / 1000.0)

def _crater_depth_km(Dfr_km):
    """
    Profundidade aproximada:
    - simples: d ≈ 0.20 * Dfr (razão típica d/D ~0.14–0.20)  :contentReference[oaicite:6]{index=6}
    - complexa (Dfr ≥ 3.2 km): d ≈ 0.4 * Dfr^0.3  (EIEP Eq. 28*)  :contentReference[oaicite:7]{index=7}
    """
    if Dfr_km is None:
        return None
    if Dfr_km < 3.2:
        return 0.20 * Dfr_km
    # relação de complexos (aprox.)
    return 0.4 * (Dfr_km ** 0.3)



def estimate_diameter_from_H(H, pV=DEFAULT_ALBEDO):
    """
    D(km) = 1329 / sqrt(pV) * 10^(-H/5)
    """
    Hn = _num(H)
    pv = _num(pV)
    if Hn is None or pv is None or pv <= 0:
        return None
    return 1329.0 / (pv ** 0.5) * (10 ** (-Hn / 5.0))

def _diameter_from_neows(neo: dict):
    try:
        k = (neo or {}).get("estimated_diameter", {}).get("kilometers", {})
        dmin = _num(k.get("estimated_diameter_min"))
        dmax = _num(k.get("estimated_diameter_max"))
        if dmin and dmax:
            return (dmin + dmax) / 2.0
        return dmax or dmin
    except Exception:
        return None

def estimate_impact_effects(neo: dict,
                            enr: dict | None,
                            velocity_kms: float | None,
                            angle_deg: float,
                            target: str,
                            override_diameter_km: float | None = None,
                            override_density_g_cm3: float | None = None,
                            override_mass_kg: float | None = None,
                            # --- NOVOS ---
                            water_depth_m: float | None = None,
                            coast_depth_m: float = COAST_DEFAULT_DEPTH_M,
                            coast_r_km_csv: str | None = None,
                            runup_factor: float = RUNUP_FACTOR,
                            dispersion_length_km: float = DISPERSION_LENGTH_KM,
                            seismic_coupling: float = SEISMIC_COUPLING_DEFAULT):
    # (código existente de resolução de v_ms, d_km, rho_g_cm3, m_kg ...)
    v_kms = _resolve_velocity_kms(neo, velocity_kms); v_ms = v_kms * 1000.0
    d_km = _resolve_diameter_km(neo, enr, override_diameter_km)
    rho_g_cm3 = override_density_g_cm3 if override_density_g_cm3 is not None else (enr or {}).get("density_g_cm3", DEFAULT_RHO_G_CM3)
    m_kg = _resolve_mass_kg(enr, override_mass_kg, d_km, rho_g_cm3)

    # energia/momento
    E_j = 0.5 * float(m_kg) * (v_ms ** 2) if (m_kg is not None) else None
    tnt_kt = (E_j / JOULES_PER_KT_TNT) if E_j is not None else None
    tnt_Mt = (E_j / JOULES_PER_MT_TNT) if E_j is not None else None
    p_Ns = (float(m_kg) * v_ms) if m_kg is not None else None

    # PI-scaling já existente:
    L_m = d_km * 1000.0 if d_km is not None else None
    Dtc_m = _crater_transient_diameter_m(L_m, v_ms, _to_kg_m3_from_g_cm3(rho_g_cm3) or 2500.0,
                                         TARGET_RHO.get((target or "rock").lower(), 2700.0),
                                         angle_deg)
    Dfr_km = _crater_final_from_transient_km(Dtc_m)
    depth_km = _crater_depth_km(Dfr_km)
    crater_type = "simple" if (Dfr_km is not None and Dfr_km < 3.2) else ("complex" if Dfr_km is not None else None)

    # --- NOVO: oceano/tsunami, só se alvo 'water' (ou se usuário informar profundidade) ---
    ocean = None
    tgt_lower = (target or "rock").lower()
    if tgt_lower in ("water", "ice") or (water_depth_m is not None):
        wdep = float(water_depth_m or OCEAN_DEFAULT_DEPTH_M)
        rlist = _parse_csv_floats(coast_r_km_csv) or [50.0, 100.0, 200.0, 500.0]
        ocean = _ocean_wavefield_from_crater(
            Dtc_m=Dtc_m,
            water_depth_m=wdep,
            coast_depth_m=coast_depth_m or COAST_DEFAULT_DEPTH_M,
            distances_km=rlist,
            nearfield_radius_factor=NEARFIELD_RADIUS_FACTOR,
            alpha_init=INITIAL_WAVE_ALPHA,
            Ld_km=dispersion_length_km or DISPERSION_LENGTH_KM,
            runup_factor=runup_factor or RUNUP_FACTOR,
        )

    # --- NOVO: sísmico (Mw a partir de E cinética × acoplamento) ---
    seismic = _seismic_from_energy(E_j, coupling=seismic_coupling or SEISMIC_COUPLING_DEFAULT)

    return {
        "inputs_resolved": {
            "diameter_km": d_km,
            "density_g_cm3": rho_g_cm3,
            "mass_kg": m_kg,
            "velocity_kms": v_kms,
            "angle_deg": angle_deg,
            "target": target,
        },
        "energy": {
            "kinetic_j": E_j,
            "tnt_kt": tnt_kt,
            "tnt_Mt": tnt_Mt,
            "momentum_Ns": p_Ns
        },
        "crater": {
            "transient_diameter_km": (Dtc_m / 1000.0) if Dtc_m is not None else None,
            "final_diameter_km": Dfr_km,
            "depth_km": depth_km,
            "type": crater_type,
            "ratio_final_to_impactor": (Dfr_km / d_km) if (Dfr_km and d_km) else None
        },
        "ocean": ocean,     # <- NOVO (quando aplicável)
        "seismic": seismic, # <- NOVO
        "notes": [
            "First-order scaling (EIEP) for cratering.",
            "Ocean: geometric spreading + dispersion + shoaling (very coarse).",
            "Seismic Mw via energy coupling; highly uncertain."
        ]
    }


# def estimate_impact_effects(neo: dict,
#                             enr: dict | None,
#                             velocity_kms: float | None,
#                             angle_deg: float,
#                             target: str,
#                             override_diameter_km: float | None = None,
#                             override_density_g_cm3: float | None = None,
#                             override_mass_kg: float | None = None):
#     # parâmetros resolvidos
#     v_kms = _resolve_velocity_kms(neo, velocity_kms)
#     v_ms = v_kms * 1000.0
#     d_km = _resolve_diameter_km(neo, enr, override_diameter_km)
#     rho_g_cm3 = override_density_g_cm3 if override_density_g_cm3 is not None else (enr or {}).get("density_g_cm3", DEFAULT_RHO_G_CM3)
#     m_kg = _resolve_mass_kg(enr, override_mass_kg, d_km, rho_g_cm3)

#     # densidades p/ cratera (projetil & alvo)
#     rho_i = _to_kg_m3_from_g_cm3(rho_g_cm3) or 2500.0
#     rho_t = TARGET_RHO.get((target or "rock").lower(), 2700.0)

#     # energia e TNT
#     E_j = 0.5 * float(m_kg) * (v_ms ** 2) if (m_kg is not None) else None
#     tnt_kt = (E_j / JOULES_PER_KT_TNT) if E_j is not None else None
#     tnt_Mt = (E_j / JOULES_PER_MT_TNT) if E_j is not None else None
#     p_Ns = (float(m_kg) * v_ms) if m_kg is not None else None

#     # Cratera (transiente e final)
#     L_m = d_km * 1000.0 if d_km is not None else None
#     Dtc_m = _crater_transient_diameter_m(L_m, v_ms, rho_i, rho_t, angle_deg)
#     Dfr_km = _crater_final_from_transient_km(Dtc_m)
#     depth_km = _crater_depth_km(Dfr_km)
#     crater_type = "simple" if (Dfr_km is not None and Dfr_km < 3.2) else ("complex" if Dfr_km is not None else None)

#     return {
#         "inputs_resolved": {
#             "diameter_km": d_km,
#             "density_g_cm3": rho_g_cm3,
#             "mass_kg": m_kg,
#             "velocity_kms": v_kms,
#             "angle_deg": angle_deg,
#             "target": target,
#         },
#         "energy": {
#             "kinetic_j": E_j,
#             "tnt_kt": tnt_kt,
#             "tnt_Mt": tnt_Mt,
#             "momentum_Ns": p_Ns
#         },
#         "crater": {
#             "transient_diameter_km": (Dtc_m / 1000.0) if Dtc_m is not None else None,
#             "final_diameter_km": Dfr_km,
#             "depth_km": depth_km,
#             "type": crater_type,
#             "ratio_final_to_impactor": (Dfr_km / d_km) if (Dfr_km and d_km) else None
#         },
#         "notes": [
#             "First-order scaling based on Collins–Melosh–Marcus (EIEP).",
#             "No atmospheric entry/airburst modelling here."
#         ]
#     }


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
ENRICH_TTL = int(os.getenv("ENRICH_TTL", "30"))  # 6h
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
    """
    Extrai o primeiro float de x (dict {"value":...} ou string p/ "2.9 ± 0.5 g/cm^3").
    """
    try:
        if isinstance(x, dict):
            x = x.get("value")
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x)
        m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", s)
        return float(m.group(0)) if m else None
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
        js = r.json()
        # Se vier LISTA, pegue o primeiro dict "útil"
        if isinstance(js, list):
            for cand in js:
                if isinstance(cand, dict):
                    return cand
            return None
        # Se vier DICT, ok
        if isinstance(js, dict):
            return js
        return None
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

def extract_phys_from_sbdb(phys: dict):
    if not isinstance(phys, dict):
        return {}
    diameter = _num(phys.get("diameter"))
    mass = _num(phys.get("mass"))
    if mass is None and phys.get("GM") is not None:
        mass = _mass_from_GM(phys.get("GM"))
    density = _num(phys.get("density") or phys.get("rho"))
    return {"diameter_km": diameter, "mass_kg": mass, "density_g_cm3": density}

def extract_phys_from_ssocard(card: dict):
    if not isinstance(card, dict):
        return {}
    p = card.get("parameters") or {}
    if not isinstance(p, dict):
        p = {}
    phys = p.get("physical") or {}
    if not isinstance(phys, dict):
        phys = {}
    diameter = _num(phys.get("diameter") or phys.get("equivalent_diameter"))
    mass = _num(phys.get("mass"))
    if mass is None and phys.get("GM") is not None:
        mass = _mass_from_GM(phys.get("GM"))
    density = _num(phys.get("density") or phys.get("rho"))
    taxo = extract_taxonomy(card)
    refs = card.get("references") or []
    bib = None
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, dict) and ref.get("bibcode"):
                bib = ref["bibcode"]
                break
    return {"diameter_km": diameter, "mass_kg": mass, "density_g_cm3": density, "taxonomy": taxo, "bibcode": bib}


G_KM3_PER_KG_S2 = 6.67430e-20  # km^3/(kg*s^2)

def _mass_from_GM(gm):
    gm_val = _num(gm)
    if gm_val is None:
        return None
    return gm_val / G_KM3_PER_KG_S2  # kg





def _label_variants(label: str, fallback: dict = None):
    """
    Gera rótulos alternativos para resolver no SsODNet/SBDB:
    - original
    - sem parênteses / número: "(99942) Apophis" -> "Apophis", "99942"
    - designation, se disponível (fallback["designation"])
    """
    lab = (label or "").strip()
    out = []
    if lab: out.append(lab)

    # remove parênteses e split
    # ex "(99942) Apophis" -> ["99942", "Apophis"]
    core = lab.replace("(", " ").replace(")", " ").strip()
    parts = [p for p in core.replace("  ", " ").split(" ") if p]
    if parts:
        # nome sem número
        no_num = " ".join([p for p in parts if not p.isdigit()])
        if no_num and no_num not in out:
            out.append(no_num)
        # só número
        only_num = next((p for p in parts if p.isdigit()), None)
        if only_num and only_num not in out:
            out.append(only_num)

    if fallback and fallback.get("designation"):
        des = str(fallback["designation"]).strip()
        if des and des not in out:
            out.append(des)

    return out

def enrich_by_label(label: str, neo_context: dict = None):
    ck = ("enrich", label)
    cached = _enrich_cache_get(ck)
    if cached is not None:
        return cached

    result = {"source": None, "mass_kg": None, "density_g_cm3": None,
              "diameter_km": None, "taxonomy": None, "bibcode": None, "note": None}
    notes = []

    # 1) Tenta SsODNet
    try:
        sid = ssod_quaero(label)
        if sid:
            card = ssod_card(sid)
            ssop = extract_phys_from_ssocard(card)
            if any([ssop.get("mass_kg"), ssop.get("density_g_cm3"), ssop.get("diameter_km")]):
                result.update(ssop)
                result["source"] = "ssodnet"
    except Exception:
        pass

    # 2) Tenta SBDB (fallback)
    try:
        if result["mass_kg"] is None or result["density_g_cm3"] is None or result["diameter_km"] is None:
            sbp = extract_phys_from_sbdb(sbdb_phys(label))
            for k, v in (sbp or {}).items():
                if result.get(k) is None and v is not None:
                    result[k] = v
            if any([result.get("mass_kg"), result.get("density_g_cm3"), result.get("diameter_km")]) and not result["source"]:
                result["source"] = "sbdb"
    except Exception:
        pass

    # 3) Fallbacks *estimados* (preencher nulos)
    # 3a) Diâmetro: usa NeoWS se ainda faltar
    if result["diameter_km"] is None and neo_context:
        d_ctx = _diameter_from_neows(neo_context)
        if d_ctx is not None:
            result["diameter_km"] = d_ctx
            notes.append("diameter from NeoWS (avg min/max)")

    # 3b) Se ainda faltar diâmetro: estima via H + albedo padrão
    if result["diameter_km"] is None:
        H_ctx = (neo_context or {}).get("absolute_magnitude_h")
        d_est = estimate_diameter_from_H(H_ctx, DEFAULT_ALBEDO)
        if d_est is not None:
            result["diameter_km"] = d_est
            notes.append(f"diameter estimated from H (pV={DEFAULT_ALBEDO})")

    # 3c) Densidade: usa taxonomia -> TAXO_RHO; se não houver, usa DEFAULT_RHO_G_CM3
    if result["density_g_cm3"] is None:
        taxo_key = str(result.get("taxonomy") or "").upper()
        rho = TAXO_RHO.get(taxo_key) if taxo_key else None
        if rho is not None:
            result["density_g_cm3"] = rho
            notes.append(f"density from taxonomy ({taxo_key})")
        else:
            result["density_g_cm3"] = DEFAULT_RHO_G_CM3
            notes.append(f"density default ({DEFAULT_RHO_G_CM3} g/cm^3)")

    # 3d) Massa: sempre que tiver diâmetro + densidade, calcula
    if result["mass_kg"] is None and result.get("diameter_km") and result.get("density_g_cm3"):
        m_est = estimate_mass_from_diameter_density(result["diameter_km"], result["density_g_cm3"])
        if m_est is not None:
            result["mass_kg"] = m_est
            notes.append("mass estimated from diameter & density")

    # Se nada veio de fontes externas, marque como estimate
    if result["source"] is None:
        result["source"] = "estimate"

    # Monte nota
    if notes:
        result["note"] = "; ".join(notes)

    # Cacheia mesmo se for estimado (evita recomputo)
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
    enrich: bool = Query(False, description="Se true, junta massa/densidade"),
    # —— NOVOS parâmetros (impacto) ——
    impact: bool = Query(False, description="Se true, calcula efeitos de impacto"),
    velocity_kms: Optional[float] = Query(None, description="Velocidade no impacto (km/s)"),
    angle_deg: float = Query(45.0, ge=1.0, le=90.0, description="Ângulo de impacto (90=vertical)"),
    target: str = Query("rock", description="Alvo: rock|sedimentary|crystalline|water|ice"),
    diameter_km: Optional[float] = Query(None, description="Sobrescreve diâmetro do projetil (km)"),
    density_g_cm3: Optional[float] = Query(None, description="Sobrescreve densidade (g/cm^3)"),
    mass_kg: Optional[float] = Query(None, description="Sobrescreve massa (kg)")
):
    params = {"api_key": NASA_KEY}
    neo = _get(f"{NASA_API}/neo/{neo_id}", params)

    if mitigations:
        neo["assessment"] = build_assessment(neo)

    enr = None
    if enrich:
        try:
            label = neo.get("name") or neo.get("designation") or str(neo_id)
            enr = enrich_by_label(label, neo_context=neo)
            neo["enrichment"] = enr
        except Exception as e:
            neo["enrichment_error"] = str(e)

    if impact:
        try:
            neo["impact"] = estimate_impact_effects(
                neo=neo,
                enr=enr,  # pode ser None; a função usa fallbacks
                velocity_kms=velocity_kms,
                angle_deg=angle_deg,
                target=target,
                override_diameter_km=diameter_km,
                override_density_g_cm3=density_g_cm3,
                override_mass_kg=mass_kg
            )
        except Exception as e:
            neo["impact_error"] = str(e)

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
    try:
        neo = _get(f"{NASA_API}/neo/{neo_id}", {"api_key": NASA_KEY})
        label = neo.get("name") or neo.get("designation") or str(neo_id)
        data = enrich_by_label(label, neo_context=neo)  # << aqui
        return {"neo_id": neo_id, "label": label, "enrichment": data}
    except Exception as e:
        return JSONResponse(status_code=502, content={
            "error": "enrichment_failed",
            "detail": str(e)
        })

@app.get("/neo/impact/{neo_id}")
def neo_impact(
    neo_id: str,
    velocity_kms: float | None = Query(None),
    angle_deg: float = Query(45.0, ge=1.0, le=90.0),
    target: str = Query("rock", description="rock|sedimentary|crystalline|water|ice"),
    enrich: bool = Query(True),
    diameter_km: float | None = Query(None),
    density_g_cm3: float | None = Query(None),
    mass_kg: float | None = Query(None),
    # --- NOVOS ---
    water_depth_m: float | None = Query(None, description="Profundidade local (m) para impactos em água; padrão=4000."),
    coast_depth_m: float = Query(COAST_DEFAULT_DEPTH_M, description="Profundidade média costeira (m)"),
    coast_r_km: str | None = Query(None, description="Lista CSV de distâncias costeiras em km (ex: 50,100,200,500)"),
    runup_factor: float = Query(RUNUP_FACTOR, description="Fator de run-up ≈ múltiplo da amplitude costeira"),
    dispersion_length_km: float = Query(DISPERSION_LENGTH_KM, description="Escala de atenuação por dispersão (km)"),
    seismic_coupling: float = Query(SEISMIC_COUPLING_DEFAULT, description="Fraç. de energia cinética → energia sísmica"),
):
    ...
    out = estimate_impact_effects(
        neo, enr,
        velocity_kms=velocity_kms,
        angle_deg=angle_deg,
        target=target,
        override_diameter_km=diameter_km,
        override_density_g_cm3=density_g_cm3,
        override_mass_kg=mass_kg,
        water_depth_m=water_depth_m,
        coast_depth_m=coast_depth_m,
        coast_r_km_csv=coast_r_km,
        runup_factor=runup_factor,
        dispersion_length_km=dispersion_length_km,
        seismic_coupling=seismic_coupling
    )
    return {"neo_id": neo_id, "label": label, "impact": out, "enrichment": enr if enrich else None}
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": "impact_estimate_failed", "detail": str(e)})

# @app.get("/neo/impact/{neo_id}")
# def neo_impact(
#     neo_id: str,
#     velocity_kms: float | None = Query(None, description="Velocidade no impacto (km/s). Se ausente, usa métrica do NEO ou 20."),
#     angle_deg: float = Query(45.0, ge=1.0, le=90.0, description="Ângulo de impacto em graus (90=vertical)."),
#     target: str = Query("rock", description="rock|sedimentary|crystalline|water|ice"),
#     enrich: bool = Query(True, description="Usa enrichment/estimativas anteriores como base"),
#     diameter_km: float | None = Query(None, description="Sobrescreve diâmetro do projetil (km)"),
#     density_g_cm3: float | None = Query(None, description="Sobrescreve densidade (g/cm^3)"),
#     mass_kg: float | None = Query(None, description="Sobrescreve massa (kg)")
# ):
#     try:
#         neo = _get(f"{NASA_API}/neo/{neo_id}", {"api_key": NASA_KEY})
#         label = neo.get("name") or neo.get("designation") or str(neo_id)
#         enr = None
#         if enrich:
#             try:
#                 enr = enrich_by_label(label, neo_context=neo)
#             except Exception as e:
#                 enr = None
#                 neo["enrichment_error"] = str(e)

#         out = estimate_impact_effects(
#             neo, enr,
#             velocity_kms=velocity_kms,
#             angle_deg=angle_deg,
#             target=target,
#             override_diameter_km=diameter_km,
#             override_density_g_cm3=density_g_cm3,
#             override_mass_kg=mass_kg
#         )
#         return {"neo_id": neo_id, "label": label, "impact": out, "enrichment": enr if enrich else None}
#     except Exception as e:
#         return JSONResponse(status_code=502, content={"error": "impact_estimate_failed", "detail": str(e)})
