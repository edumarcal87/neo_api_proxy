# NeoWS Python Proxy + Mitigations (FastAPI)

Proxy para NASA NeoWS com CORS, cache e **avaliação + mitigações** (heurística educacional).

## Endpoints
- `GET /health`
- `GET /neo/feed?start_date=YYYY-MM-DD[&end_date=YYYY-MM-DD][&mitigations=true]`
- `GET /neo/{neo_id}[?mitigations=true]`
- `GET /neo/browse?page=0&size=20[&mitigations=true]`
- `GET /neo/hazardous?page=0&size=50&min_diameter_km=0.0&max_miss_distance_km=10000000[&mitigations=true]`
- `GET /neo/assess/{neo_id}`

---

### ✅ Testes rápidos

- Saúde: `GET https://neo-api-proxy.onrender.com/health`  
- Feed: `GET https://neo-api-proxy.onrender.com/neo/feed?start_date=2025-09-30&end_date=2025-10-01`  
- Detalhe: `GET https://neo-api-proxy.onrender.com/neo/3726710`  
- Browse: `GET https://neo-api-proxy.onrender.com/neo/browse?page=0&size=10`

---

## 🖥️ Usando no GitHub Pages (front-end)

No seu HTML/JS:
```html
<script>
  const API_BASE = "https://neo-api-proxy.onrender.com";

  async function getFeed(start, end) {
    const u = new URL(API_BASE + "/neo/feed");
    u.searchParams.set("start_date", start);
    if (end) u.searchParams.set("end_date", end);
    const res = await fetch(u);
    if (!res.ok) throw new Error("Erro ao buscar feed");
    return res.json();
  }

  async function getNeo(neoId) {
    const res = await fetch(API_BASE + "/neo/" + neoId);
    if (!res.ok) throw new Error("Erro ao buscar NEO");
    return res.json();
  }

  async function browse(page=0, size=10) {
    const u = new URL(API_BASE + "/neo/browse");
    u.searchParams.set("page", page);
    u.searchParams.set("size", size);
    const res = await fetch(u);
    if (!res.ok) throw new Error("Erro ao buscar browse");
    return res.json();
  }

  // Exemplo:
  // getFeed("2025-09-30", "2025-10-01").then(console.log);
</script>
```

---

## 🧪 Dev local

1. Crie um `.env` baseado em `.env.example` e defina `NASA_API_KEY`.  
2. `pip install -r requirements.txt`  
3. `uvicorn main:app --reload`  
4. Acesse `http://127.0.0.1:8000/` para ver a doc automática.

---
