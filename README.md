# NeoWS Python Proxy — Advanced (Mitigations + Filters + Enrichment + Impact + Ocean/Tsunami + Seismic)

Serviço **FastAPI** que expõe e enriquece dados da **NASA NeoWS** e adiciona camadas de:
- **Mitigations/Assessment** (heurística de risco por NEO)
- **Filtros avançados** (varredura do `browse` com filtros server-side)
- **Enrichment** (massa, densidade, diâmetro, taxonomia, bibcode) via **SsODNet** e **JPL SBDB**, com **estimativas automáticas**
- **Impact** (estimativa de impacto na Terra): energia (J, kt/Mt TNT), cratera (transiente/final), profundidade
  - Impacto em **oceano** com estimativa de **tsunami** (amplitude em águas profundas, amplitude costeira e *run-up*)
  - Estimativa **sísmica** (*Mw*) a partir de acoplamento de energia

> ⚠️ **Aviso**: Projeto **educacional**. As avaliações/estimativas são de 1ª ordem (screening) e **não** substituem análises oficiais.

**URL BASE DO SERVIÇO: ** https://neo-api-proxy.onrender.com
---

## Sumário
- [Arquitetura](#arquitetura)
- [Variáveis de Ambiente](#variáveis-de-ambiente)
- [Instalação/Execução Local](#instalaçãoexecução-local)
- [Deploy rápido (Render)](#deploy-rápido-render)
- [Demo (GitHub Pages + CORS)](#demo-github-pages--cors)
- [Endpoints](#endpoints)
  - [/health](#get-health)
  - [/neo/feed](#get-neofeed)
  - [/neo/{neo_id}](#get-neoneo_id)
  - [/neo/browse](#get-neobrowse)
  - [/neo/filter](#get-neofilter)
  - [/neo/hazardous](#get-neohazardous)
  - [/neo/enrich/{neo_id}](#get-neoenrichneo_id)
  - [/neo/impact/{neo_id}](#get-neoimpactneo_id)
- [Modelos de Resposta](#modelos-de-resposta)
- [Erros & Tratamento](#erros--tratamento)
- [Notas Técnicas](#notas-técnicas)
- [Licença](#licença)

---

## Arquitetura
- **FastAPI** + **uvicorn**.
- **Proxy NeoWS** com cache (TTL) e CORS.
- **Enrichment** (ordem de tentativa):  
  1) **SsODNet** (`quaero` → `ssocard/{id}`)  
  2) **JPL SBDB** (`sbdb.api?sstr=...&phys-par=1`)  
  3) **Estimativas** quando faltar dado:  
     - diâmetro: NeoWS (média min/max) **ou** por **H + albedo** (`DEFAULT_ALBEDO`);  
     - densidade: por **taxonomia** (mapa interno) **ou** **padrão** (`DEFAULT_RHO_G_CM3`);  
     - massa: esfera com (D, ρ).
- **Impact**:
  - Crateras por *pi-scaling* (Collins–Melosh–Marcus); energia/momento por ½·m·v²; equivalentes TNT.
  - **Oceano/Tsunami (novo)**: amplitude inicial ∝ cratera transiente; propagação com espalhamento geométrico + termo de dispersão; *shoaling* costeiro (lei de Green aproximada) e *run-up* ≈ fator × amplitude costeira.
  - **Sísmico (novo)**: magnitude momento *Mw* estimada de fração de acoplamento da energia cinética → energia sísmica.

---

## Variáveis de Ambiente

| VAR | Descrição | Padrão |
|---|---|---|
| `NASA_API_KEY` | Chave da API da NASA (api.nasa.gov) | `DEMO_KEY` |
| `CORS_ORIGINS` | Origens permitidas (vírgula) | `*` |
| `CACHE_TTL` | Cache do proxy NeoWS (s) | `300` |
| `ENRICH_TTL` | Cache do enrichment (s) | `21600` (6h) |
| `DEFAULT_RHO_G_CM3` | Densidade padrão p/ estimar (g/cm³) | `2.5` |
| `DEFAULT_ALBEDO` | Albedo p/ estimar D via H | `0.14` |

---

## Instalação/Execução Local
```bash
python -m venv .venv && source .venv/bin/activate  # (Windows: .venv\Scripts\activate)
pip install -r requirements.txt
uvicorn main:app --reload
# abre http://127.0.0.1:8000/docs
```

---

## Deploy rápido (Render)
1. **Build**: `pip install -r requirements.txt`  
2. **Start**: `uvicorn main:app --host 0.0.0.0 --port $PORT`  
3. **Env vars**: conforme tabela acima.  
4. Configure `CORS_ORIGINS` com o seu domínio do GitHub Pages (ex.: `https://seuusuario.github.io`).

---

## Demo (GitHub Pages + CORS)
- Publique a pasta `demo-site` no **GitHub Pages**.
- No serviço (Render), defina `CORS_ORIGINS` para o domínio do Pages.
- Em `demo-site/index.html`, informe a URL do serviço e teste:
  - **Detalhe** com *mitigations / enrichment / impact* (incluindo oceano & sísmico);
  - **Enrichment** direto;
  - **Filter/Hazardous** com enrich.

---

## Endpoints

### `GET /health`
**Ping** do serviço.  
**Resposta**:
```json
{ "status": "ok" }
```

---

### `GET /neo/feed`
Espelha o `feed` da NeoWS; pode anexar avaliação/mitigações.

**Parâmetros**:
- `start_date` (YYYY-MM-DD) **obrigatório**
- `end_date` (YYYY-MM-DD) opcional
- `mitigations` (bool) — **default**: `false` (se `true`, adiciona `"assessment"` em cada NEO)

**Exemplo**
```bash
curl -s "$BASE/neo/feed?start_date=2025-01-01&end_date=2025-01-07&mitigations=true" | jq
```

---

### `GET /neo/{neo_id}`
Detalhe do NEO (NeoWS) com **mitigations**, **enrichment** e **impact** opcionais.

**Parâmetros**
- `mitigations` (bool) — **default**: `true`
- `enrich` (bool) — **default**: `false` → inclui `"enrichment"`
- `impact` (bool) — **default**: `false` → inclui `"impact"`

**Impact (extras opcionais)**
- **Básicos**:  
  `velocity_kms` (float) — se ausente, usa métrica do NEO ou **20**,  
  `angle_deg` (float, 1–90) — **default 45**,  
  `target` (`rock|sedimentary|crystalline|water|ice`) — **default rock**,  
  `diameter_km`, `density_g_cm3`, `mass_kg` — sobrescrevem valores resolvidos.
- **Oceano/Tsunami (novo)**:  
  `water_depth_m` — profundidade local (m). Se ausente e `target=water|ice`, assume ~**4000** (oceano aberto).  
  `coast_depth_m` — profundidade costeira típica (m) (**50**).  
  `coast_r_km` — lista **CSV** de distâncias costeiras para amostrar ondas (ex.: `50,100,200,500`).  
  `runup_factor` — fator de *run-up* (default **2.0**).  
  `dispersion_length_km` — escala de atenuação por dispersão (default **1000**).
- **Sísmico (novo)**:  
  `seismic_coupling` — fração de acoplamento energia cinética → energia sísmica (tipicamente **1e-5–1e-3**, default **1e-4**).

**Exemplos**
```bash
# Detalhe com enrichment + impacto (20 km/s, 45°, rocha)
curl -s "$BASE/neo/3726710?enrich=true&impact=true&velocity_kms=20&angle_deg=45&target=rock" | jq

# Detalhe + impacto em água, amostrando ondas em 50/100/200 km
curl -s "$BASE/neo/3726710?impact=true&target=water&water_depth_m=4000&coast_r_km=50,100,200&coast_depth_m=50&velocity_kms=20&angle_deg=45" | jq
```

---

### `GET /neo/browse`
Espelha o `browse` da NeoWS; pode anexar **mitigations** e/ou **enrichment** (cuidado com latência).

**Parâmetros**
- `page` (int) — **default 0**
- `size` (int) — **default 20**
- `mitigations` (bool) — **default false**
- `enrich` (bool) — **default false**

**Exemplo**
```bash
curl -s "$BASE/neo/browse?page=0&size=10&enrich=true" | jq
```

---

### `GET /neo/filter`
Varre `pages × size` do `browse` e aplica **filtros server-side**; pode anexar **mitigations** e **enrichment**.

**Parâmetros de varredura**
- `pages` (1–50) — **default 3**
- `size` (1–100) — **default 50**
- `limit` (1–1000) — **default 200**

**Filtros (opcionais)**
- `hazardous` (bool)
- `min_diameter_km`, `max_diameter_km`
- `min_miss_km`, `max_miss_km`
- `min_rel_vel_kms`, `max_rel_vel_kms`
- `days_min`, `days_max`
- `mag_h_min`, `mag_h_max`
- `approach_body` — ex.: `Earth`
- `date_from`, `date_to`

**Extras**
- `mitigations` (bool) — **default true**
- `enrich` (bool) — **default false**

**Exemplos**
```bash
# Grandes (>0.3km), bem próximos (<1e6 km), até 365d, apenas Terra
curl -s "$BASE/neo/filter?min_diameter_km=0.3&max_miss_km=1000000&days_max=365&approach_body=Earth&enrich=true" | jq

# Apenas hazardous, velocidade >20 km/s, até 50 itens
curl -s "$BASE/neo/filter?hazardous=true&min_rel_vel_kms=20&limit=50" | jq
```

---

### `GET /neo/hazardous`
Atalho para potencialmente perigosos, com filtros extras.

**Parâmetros**
- `page` (int) — **default 0**
- `size` (int) — **default 50**
- `min_diameter_km` (float) — **default 0.0**
- `max_miss_distance_km` (float) — **default 10000000.0**
- `mitigations` (bool) — **default true**
- Extras: `max_diameter_km`, `min_miss_distance_km`, `min_rel_vel_kms`, `max_rel_vel_kms`, `days_max`, `approach_body`, `enrich`

**Exemplo**
```bash
curl -s "$BASE/neo/hazardous?min_diameter_km=0.1&max_miss_distance_km=1500000&enrich=true" | jq
```

---

### `GET /neo/enrich/{neo_id}`
Retorna **somente** o bloco de **enrichment** (massa, densidade, diâmetro, taxonomia, bibcode, fonte/nota).  
Quando dados não existem, **gera estimativas** (H+albedo, taxonomia ou padrão).

**Exemplo**
```bash
curl -s "$BASE/neo/enrich/3726710" | jq
```

---

### `GET /neo/impact/{neo_id}`
Estimador de **Impact** (energia, TNT, cratera transiente/final/profundidade).  
Inclui **oceano/tsunami** e **sísmico** quando aplicável.

**Parâmetros**
- **Básicos**: `velocity_kms`, `angle_deg` (1–90; **45**), `target` (`rock|sedimentary|crystalline|water|ice`, **rock**), `diameter_km`, `density_g_cm3`, `mass_kg`, `enrich` (bool, **true**).
- **Oceano/Tsunami (novo)**: `water_depth_m`, `coast_depth_m`, `coast_r_km` (CSV), `runup_factor`, `dispersion_length_km`.
- **Sísmico (novo)**: `seismic_coupling`.

**Exemplos**
```bash
# Padrão (20 km/s, 45°, rocha)
curl -s "$BASE/neo/impact/3726710" | jq

# Impacto em água, ondas em 50/100/200 km e run-up
curl -s "$BASE/neo/impact/3726710?target=water&water_depth_m=4000&coast_r_km=50,100,200&coast_depth_m=50&velocity_kms=20&angle_deg=45" | jq

# Cenário com acoplamento sísmico maior
curl -s "$BASE/neo/impact/3726710?target=rock&velocity_kms=25&angle_deg=30&seismic_coupling=0.0003" | jq
```

---

## Modelos de Resposta

### Bloco `enrichment`
```json
{
  "source": "ssodnet|sbdb|estimate",
  "mass_kg": 1.23e+11,
  "density_g_cm3": 2.5,
  "diameter_km": 0.42,
  "taxonomy": "S",
  "bibcode": "2021Icar..xxx..yyyZ",
  "note": "diameter from NeoWS (avg min/max); mass estimated from diameter & density; density default (2.5 g/cm^3)"
}
```
> Se as fontes externas não tiverem dados, o serviço **preenche estimativas** e marca `source: "estimate"` com `note`.

### Bloco `impact` (com oceano e sísmico)
```json
{
  "inputs_resolved": {
    "diameter_km": 0.42,
    "density_g_cm3": 2.5,
    "mass_kg": 1.23e+11,
    "velocity_kms": 20.0,
    "angle_deg": 45.0,
    "target": "water"
  },
  "energy": {
    "kinetic_j": 2.46e+19,
    "tnt_kt": 5882.3,
    "tnt_Mt": 5.882,
    "momentum_Ns": 2.46e+15
  },
  "crater": {
    "transient_diameter_km": 5.6,
    "final_diameter_km": 7.0,
    "depth_km": 1.4,
    "type": "simple",
    "ratio_final_to_impactor": 16.7
  },
  "ocean": {
    "initial_amp_m": 550.0,
    "nearfield_radius_km": 7.0,
    "far_field": [
      { "distance_km": 50,  "deep_water_amp_m": 30.2, "coastal_amp_m": 67.9, "runup_m": 135.8 },
      { "distance_km": 100, "deep_water_amp_m": 14.8, "coastal_amp_m": 33.2, "runup_m": 66.4 }
    ]
  },
  "seismic": { "Mw": 6.8, "E_s_j": 2.4e+15, "coupling": 0.0001 },
  "notes": [
    "First-order scaling (EIEP) for cratering.",
    "Ocean: geometric spreading + dispersion + shoaling (very coarse).",
    "Seismic Mw via energy coupling; highly uncertain."
  ]
}
```

---

## Erros & Tratamento
- **Upstream (SsODNet/SBDB)** indisponíveis ⇒ `502`:
```json
{ "error": "enrichment_failed" | "impact_estimate_failed", "detail": "..." }
```
- **Endpoints resilientes**:
  - `/neo/{id}?enrich=true` → adiciona `enrichment_error` se enrichment falhar (sem 500).
  - `/neo/{id}?impact=true` → adiciona `impact_error` se impacto falhar (sem 500).

---

## Notas Técnicas
- **CORS**: defina `CORS_ORIGINS` para o domínio do **GitHub Pages**.
- **Cache**: Proxy NeoWS (`CACHE_TTL`) e Enrichment (`ENRICH_TTL`).
- **Estimativas**: D (NeoWS ou H+albedo), ρ (taxonomia ou padrão), m (esfera).
- **Impact**:
  - Energia/momento: ½·m·v²; TNT (1 kt = 4.184×10¹² J).
  - Cratera: *pi-scaling* (transiente), **final ≈ 1.25 × transiente**; profundidade: ~0.20·D (simples) / `0.4·D^0.3` (complexa).
  - **Oceano/Tsunami**: amplitude inicial ≈ 0.10×D_tc; propagação ~ (r₀/R)·exp(-R/L_d); *shoaling* ~ (h_deep/h_coast)^(1/4); *run-up* ≈ fator × amplitude costeira.
  - **Sísmico**: `E_s = coupling × E_kin`; `Mw ≈ (log10(E_s) − 4.8)/1.5` (Gutenberg–Richter/Kanamori aproximado).
- **Limites**: sem modelo de entrada atmosférica/airburst, sem batimetria real, sem refração/difração costeira; Mw com **grande incerteza** (experimente 1e-5 a 1e-3).

---

## Licença
MIT — use livremente.
