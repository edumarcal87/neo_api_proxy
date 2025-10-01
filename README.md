# NeoWS Python Proxy (FastAPI)

Proxy leve para a **NASA NeoWs** (Near Earth Object Web Service), com **CORS controlado** e **cache simples**.
Ideal para usar **GitHub Pages** no front-end e este serviço como backend.

## 🧱 Rotas

- `GET /health` → status
- `GET /neo/feed?start_date=YYYY-MM-DD[&end_date=YYYY-MM-DD]`
- `GET /neo/{neo_id}`
- `GET /neo/browse?page=0&size=20`

---

## 🚀 Passo a passo (Render.com)

> Pré-requisitos: conta no GitHub + chave da NASA (grátis em https://api.nasa.gov).

1. **Crie um repositório no GitHub** e suba estes arquivos (`main.py`, `requirements.txt`, `Procfile`, etc.).  
2. No **Render**, clique em **New + → Web Service → Connect a repository** e selecione seu repo.  
3. **Runtime**: *Python 3.11*  
   - **Build Command**: `pip install -r requirements.txt`  
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`  
4. Em **Environment** (variáveis):  
   - `NASA_API_KEY` → sua chave real  
   - `CORS_ORIGINS` → `https://SEUusuario.github.io` (coloque sua URL do GitHub Pages; pode listar várias separadas por vírgula)  
   - (opcional) `CACHE_TTL` → `300`  
5. Faça o deploy; você terá uma URL pública, ex.: `https://neo-proxy.onrender.com`.

### ✅ Testes rápidos

- Saúde: `GET https://SEU_SERVICO/health`  
- Feed: `GET https://SEU_SERVICO/neo/feed?start_date=2025-09-30&end_date=2025-10-01`  
- Detalhe: `GET https://SEU_SERVICO/neo/3726710`  
- Browse: `GET https://SEU_SERVICO/neo/browse?page=0&size=10`

---

## 🖥️ Usando no GitHub Pages (front-end)

No seu HTML/JS:
```html
<script>
  const API_BASE = "https://SEU_SERVICO"; // ex.: https://neo-proxy.onrender.com

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

## 🔧 Notas
- A **chave da NASA** não é sensível, mas usar o proxy permite **CORS** e um **cache** que reduz latência/uso de quota.  
- Ajuste `CACHE_TTL` conforme necessário.  
- Se preferir Railway/Fly/Cloud Run, basta manter o `Start Command` do `uvicorn` e configurar as variáveis equivalentes.
