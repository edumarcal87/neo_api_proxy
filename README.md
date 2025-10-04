# NeoWS Python Proxy — Advanced (Mitigations + Filters + Enrichment)

Endpoints:
- `GET /health`
- `GET /neo/feed?start_date=YYYY-MM-DD[&end_date=YYYY-MM-DD][&mitigations=true]`
- `GET /neo/{neo_id}[?mitigations=true][&enrich=true]`
- `GET /neo/browse?page=0&size=20[&mitigations=true][&enrich=true]`
- `GET /neo/filter?…` (filtros + `mitigations` + `enrich`)
- `GET /neo/hazardous?…` (atalho com filtros + `enrich`)
- `GET /neo/enrich/{neo_id}` (somente enriquecimento)

**Enrichment** usa SsODNet (quaero/ssocard) e JPL SBDB (phys-par). 
Se faltar massa, estima via diâmetro + densidade (ou densidade típica por taxonomia).

Env vars:
- `NASA_API_KEY`, `CORS_ORIGINS`, `CACHE_TTL`, `ENRICH_TTL`
