# NeoWS Python Proxy — Advanced (Mitigations + Filters + Enrichment + Impact)

Serviço FastAPI que expõe e enriquece dados da **NASA NeoWS** com:

- **CORS** configurável (para uso com **GitHub Pages**)
- **Cache** em memória
- **Avaliação & Mitigações** heurísticas por NEO
- **Filtros avançados** (varredura de páginas do `browse`)
- **Enrichment** (massa, densidade, diâmetro, taxonomia, bibcode) via **SsODNet** e **JPL SBDB**, com **estimativas automáticas**
- **Impact** (estimador de impacto na Terra): velocidade, energia (J, kt/Mt TNT), cratera (transiente/final), profundidade, etc.

> ⚠️ **Aviso**: Este projeto tem fins **educacionais**. As avaliações e estimativas não substituem análises oficiais.

---

## Sumário

- [Arquitetura](#arquitetura)
- [Variáveis de Ambiente](#variáveis-de-ambiente)
- [Deploy rápido (Render)](#deploy-rápido-render)
- [Demo local (GitHub Pages + CORS)](#demo-local-github-pages--cors)
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
- **Enrichment**:
  - 1º: **SsODNet** (`quaero` → `ssocard/{id}`)
  - 2º: **JPL SBDB** (`sbdb.api?sstr=...&phys-par=1`)
  - 3º: **Estimativas**:
    - diâmetro pelo NeoWS (média min/max) ou por **H+albedo**,
    - densidade por **taxonomia** (mapa interno) ou **padrão**,
    - massa por **esfera** (ρ·4/3·π·(D/2)^3).
- **Impact**: *pi-scaling* (Collins–Melosh–Marcus) para crateras; energia/momento de ½mv²; equivalentes TNT.

---

## Variáveis de Ambiente

| VAR | Descrição | Padrão |
|---|---|---|
| `NASA_API_KEY` | Chave da API da NASA (api.nasa.gov) | `DEMO_KEY` (limites) |
| `CORS_ORIGINS` | Origens permitidas (separe por vírgula). Ex.: `https://SEUusuario.github.io` | `*` |
| `CACHE_TTL` | Cache do proxy (segundos) | `300` |
| `ENRICH_TTL` | Cache do enrichment (segundos) | `21600` (6h) |
| `DEFAULT_RHO_G_CM3` | Densidade padrão p/ estimativa (g/cm³) | `2.5` |
| `DEFAULT_ALBEDO` | Albedo padrão p/ estimar D via H | `0.14` |

---

## Deploy rápido (Render)

1. **Build**: `pip install -r requirements.txt`  
2. **Start**: `uvicorn main:app --host 0.0.0.0 --port $PORT`  
3. **Env vars**: conforme tabela acima.

---

## Demo local (GitHub Pages + CORS)

- Publique a pasta `demo-site` no seu **GitHub Pages**.
- Defina `CORS_ORIGINS=https://SEUusuario.github.io` no serviço (Render).
- Abra `demo-site/index.html`, insira a URL do serviço e teste:

  - Detalhe com *mitigations/enrichment/impact*
  - Enrichment direto
  - Filter/Hazardous com enrich

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
**Descrição**: espelha o `feed` da NeoWS; pode anexar avaliação/mitigações.

**Parâmetros**:
- `start_date` (YYYY-MM-DD) **obrigatório**
- `end_date` (YYYY-MM-DD) opcional
- `mitigations` (bool) — **default**: `false` (se `true`, adiciona `"assessment"` em cada NEO)

**Exemplo**:
```bash
curl -s "$BASE/neo/feed?start_date=2025-01-01&end_date=2025-01-07&mitigations=true" | jq
```

---

### `GET /neo/{neo_id}`
**Descrição**: detalhe do NEO (NeoWS) com opções de **mitigations**, **enrichment** e **impact**.

**Parâmetros**:
- `mitigations` (bool) — **default**: `true`
- `enrich` (bool) — **default**: `false` → inclui `"enrichment"`
- `impact` (bool) — **default**: `false` → inclui `"impact"`
- (impact extras — opcionais):
  - `velocity_kms` (float) — se ausente, usa métrica ou 20 km/s
  - `angle_deg` (float, 1–90) — **default**: `45`
  - `target` (`rock|sedimentary|crystalline|water|ice`) — **default**: `rock`
  - `diameter_km`, `density_g_cm3`, `mass_kg` — sobrescrevem valores do cálculo

**Exemplos**:
```bash
# Detalhe com enrichment + impacto (20 km/s, 45°, rocha)
curl -s "$BASE/neo/3726710?enrich=true&impact=true&velocity_kms=20&angle_deg=45&target=rock" | jq

# Detalhe com impacto + overrides
curl -s "$BASE/neo/3726710?impact=true&diameter_km=0.35&density_g_cm3=3.0&velocity_kms=25&angle_deg=30" | jq
```

---

### `GET /neo/browse`
**Descrição**: espelha o `browse` da NeoWS; pode anexar **mitigations** e/ou **enrichment** (cuidado com latência).

**Parâmetros**:
- `page` (int) — **default**: `0`
- `size` (int) — **default**: `20`
- `mitigations` (bool) — **default**: `false`
- `enrich` (bool) — **default**: `false`

**Exemplo**:
```bash
curl -s "$BASE/neo/browse?page=0&size=10&enrich=true" | jq
```

---

### `GET /neo/filter`
**Descrição**: varre `pages × size` do `browse` e aplica **filtros server-side**; pode anexar **mitigations** e **enrichment**.

**Parâmetros de varredura**:
- `pages` (1–50) — **default**: `3`
- `size` (1–100) — **default**: `50`
- `limit` (1–1000) — **default**: `200`

**Filtros** (qualquer um opcional):
- `hazardous` (bool)
- `min_diameter_km`, `max_diameter_km`
- `min_miss_km`, `max_miss_km`
- `min_rel_vel_kms`, `max_rel_vel_kms`
- `days_min`, `days_max` — até a **próxima** aproximação
- `mag_h_min`, `mag_h_max` — magnitude absoluta H
- `approach_body` — ex.: `Earth`
- `date_from`, `date_to` — janela (considera **qualquer** aproximação que caia no intervalo)

**Extras**:
- `mitigations` (bool) — **default**: `true`
- `enrich` (bool) — **default**: `false`

**Exemplos**:
```bash
# Grandes (>0.3km), bem próximos (<1e6 km), até 365d, apenas Terra
curl -s "$BASE/neo/filter?min_diameter_km=0.3&max_miss_km=1000000&days_max=365&approach_body=Earth&enrich=true" | jq

# Apenas hazardous, velocidade >20 km/s, até 50 itens
curl -s "$BASE/neo/filter?hazardous=true&min_rel_vel_kms=20&limit=50" | jq
```

---

### `GET /neo/hazardous`
**Descrição**: atalho para potencialmente perigosos, com filtros extras.

**Parâmetros**:
- `page` (int) — **default**: `0`
- `size` (int) — **default**: `50`
- `min_diameter_km` (float) — **default**: `0.0`
- `max_miss_distance_km` (float) — **default**: `10000000.0`
- `mitigations` (bool) — **default**: `true`
- **Extras opcionais**: `max_diameter_km`, `min_miss_distance_km`, `min_rel_vel_kms`, `max_rel_vel_kms`, `days_max`, `approach_body`, `enrich`

**Exemplo**:
```bash
curl -s "$BASE/neo/hazardous?min_diameter_km=0.1&max_miss_distance_km=1500000&enrich=true" | jq
```

---

### `GET /neo/enrich/{neo_id}`
**Descrição**: retorna **somente** o bloco de **enrichment** (massa, densidade, diâmetro, taxonomia, bibcode, fonte/nota).  
**Obs.**: quando dados não existem, **gera estimativas** (H+albedo, taxonomia ou padrão).

**Exemplo**:
```bash
curl -s "$BASE/neo/enrich/3726710" | jq
```

---

### `GET /neo/impact/{neo_id}`
**Descrição**: estimador de **Impact** (energia, TNT equivalente, cratera transiente/final/profundidade), usando dados do **enrichment** (se disponível) ou **estimativas automáticas**.

**Parâmetros**:
- `velocity_kms` (float) — se ausente, usa métrica do NEO ou `20`
- `angle_deg` (float, 1–90) — **default**: `45`
- `target` (`rock|sedimentary|crystalline|water|ice`) — **default**: `rock`
- `diameter_km`, `density_g_cm3`, `mass_kg` — sobrescrevem os valores
- `enrich` (bool) — **default**: `true` (usa enrichment/estimativas como base)

**Exemplos**:
```bash
# Padrão (20 km/s, 45°, rocha)
curl -s "$BASE/neo/impact/3726710" | jq

# Cenário customizado
curl -s "$BASE/neo/impact/3726710?velocity_kms=25&angle_deg=30&target=sedimentary&density_g_cm3=3.0" | jq
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

### Bloco `impact`
```json
{
  "inputs_resolved": {
    "diameter_km": 0.42,
    "density_g_cm3": 2.5,
    "mass_kg": 1.23e+11,
    "velocity_kms": 20.0,
    "angle_deg": 45.0,
    "target": "rock"
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
    "type": "simple|complex",
    "ratio_final_to_impactor": 16.7
  },
  "notes": [
    "First-order scaling based on Collins–Melosh–Marcus (EIEP).",
    "No atmospheric entry/airburst modelling here."
  ]
}
```

---

## Erros & Tratamento

- **Upstream (SsODNet/SBDB)** indisponíveis ⇒ `502` com payload:
  ```json
  { "error": "enrichment_failed" | "impact_estimate_failed", "detail": "..." }
  ```
- **Endpoints resilientes**:
  - `/neo/{id}?enrich=true` → se enrichment falhar, adiciona `enrichment_error` no JSON (sem derrubar o endpoint).
  - `/neo/{id}?impact=true` → se impact falhar, adiciona `impact_error`.

---

## Notas Técnicas

- **CORS**: defina `CORS_ORIGINS` para o seu domínio do **GitHub Pages** (ex.: `https://seuusuario.github.io`).
- **Cache**:
  - proxy NeoWS: `CACHE_TTL`
  - enrichment: `ENRICH_TTL` (alto recomendado: 6h+)
- **Estimativas**:
  - diâmetro: NeoWS (média min/max) → H+albedo (`DEFAULT_ALBEDO`)
  - densidade: taxonomia (mapa interno) → `DEFAULT_RHO_G_CM3`
  - massa: esfera com (D, ρ)
- **Impact**:
  - Energia/momento: ½ m v²
  - Cratera: *pi-scaling* (transiente), `final ≈ 1.25 × transiente` (Terra)
  - Profundidade aproximada: ~0.20·D (simples) / `0.4·D^0.3` (complexa)
  - **Sem** modelo atmosférico; objetos pequenos podem não formar cratera (airburst).

---

## Licença

MIT — use à vontade, com atribuição.
