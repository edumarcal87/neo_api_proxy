# NeoWS Proxy — Mitigations + Filters + Enrichment

Este serviço expõe a NASA NeoWS com CORS, cache, **avaliação/mitigações** e um endpoint **/neo/filter** com filtros ricos.

## Endpoints
- `GET /health`
- `GET /neo/feed?start_date=YYYY-MM-DD[&end_date=YYYY-MM-DD][&mitigations=true]`
- `GET /neo/{neo_id}[?mitigations=true][&enrich=true]`
- `GET /neo/browse?page=0&size=20[&mitigations=true][&enrich=true]`
- `GET /neo/filter?…` (filtros + `mitigations` + `enrich`)
- `GET /neo/hazardous?…` (atalho com filtros + `enrich`)
- `GET /neo/enrich/{neo_id}` (somente enriquecimento)

### Filtros suportados em `/neo/filter`
- `hazardous` (true/false)
- `min_diameter_km`, `max_diameter_km`
- `min_miss_km`, `max_miss_km`
- `min_rel_vel_kms`, `max_rel_vel_kms`
- `days_min`, `days_max` (até a aproximação mais próxima)
- `mag_h_min`, `mag_h_max` (Magnitude H)
- `approach_body` (ex.: Earth)
- `date_from`, `date_to` (YYYY-MM-DD) — janela de data para qualquer *close approach*
- `pages` (padrão 3), `size` (padrão 50), `limit` (padrão 200)
- `mitigations` (true/false)

**Enrichment** usa SsODNet (quaero/ssocard) e JPL SBDB (phys-par). 
Se faltar massa, estima via diâmetro + densidade.

### Exemplos
- Maiores que 300m, próximos (< 1e6 km), janela até 365 dias, apenas Terra:
```
/neo/filter?min_diameter_km=0.3&max_miss_km=1000000&days_max=365&approach_body=Earth
```
- Potencialmente perigosos, velocidade relativa > 20 km/s, limite 50:
```
/neo/filter?hazardous=true&min_rel_vel_kms=20&limit=50
```

## Deploy (Render)
- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Env:
  - `NASA_API_KEY` (https://api.nasa.gov)
  - `CORS_ORIGINS` (ex.: https://SEUusuario.github.io)
  - `CACHE_TTL` (opcional)
  - `ENRICH_TTL` (opcional)