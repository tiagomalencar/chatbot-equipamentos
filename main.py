import os
import json
import re
import math
import unicodedata
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

BASE_DIR = os.path.dirname(__file__)

# ── Load all data ──
with open(os.path.join(BASE_DIR, "municipios_data.json"), "r", encoding="utf-8") as f:
    MUNICIPIOS = json.load(f)

with open(os.path.join(BASE_DIR, "mun_duplicados.json"), "r", encoding="utf-8") as f:
    MUN_DUPLICADOS = json.load(f)

with open(os.path.join(BASE_DIR, "total_uf_brasil.json"), "r", encoding="utf-8") as f:
    TOTAL_UF_BRASIL = json.load(f)

# Index municipalities
MUN_BY_IBGE = {m["ibge"]: m for m in MUNICIPIOS}
MUN_BY_NOME = {}
for m in MUNICIPIOS:
    MUN_BY_NOME.setdefault(m["municipio"].upper().strip(), []).append(m)

EQUIPAMENTOS = [
    "Doppler Vascular", "Espirometro Digital", "Eletrocardiografo Digital",
    "Eletrocautério (Bisturi Elétrico)", "Desfibrilador Externo Automático (DEA)",
    "Laser terapêutico de baixa potência", "Fotoforo - Foco de Luz de Cabeça",
    "Otoscopio", "Dinamometro Digital", "Balança Digital", "Dermatoscopio Digital",
    "Câmara Fria para conservação de vacinas", "Retinografo Portátil",
    "Tábua de Propriocepção", "Ultrassom para fisioterapia", "TENS e FES 60%",
    "Cadeira de Rodas"
]

UFS = list(set(m["uf"] for m in MUNICIPIOS))

UF_NOMES = {
    "ACRE": "AC", "ALAGOAS": "AL", "AMAPA": "AP", "AMAZONAS": "AM",
    "BAHIA": "BA", "CEARA": "CE", "DISTRITO FEDERAL": "DF",
    "ESPIRITO SANTO": "ES", "GOIAS": "GO", "MARANHAO": "MA",
    "MATO GROSSO": "MT", "MATO GROSSO DO SUL": "MS", "MINAS GERAIS": "MG",
    "PARA": "PA", "PARAIBA": "PB", "PARANA": "PR", "PERNAMBUCO": "PE",
    "PIAUI": "PI", "RIO DE JANEIRO": "RJ", "RIO GRANDE DO NORTE": "RN",
    "RIO GRANDE DO SUL": "RS", "RONDONIA": "RO", "RORAIMA": "RR",
    "SANTA CATARINA": "SC", "SAO PAULO": "SP", "SERGIPE": "SE", "TOCANTINS": "TO",
}

# Totals indexed by UF
TOTAL_BY_UF = {t["uf"]: t for t in TOTAL_UF_BRASIL}

# ── Load PDF/FAQ chunks ──
PDF_CHUNKS = []
chunks_path = os.path.join(BASE_DIR, "pdf_chunks.json")
if os.path.exists(chunks_path):
    with open(chunks_path, "r", encoding="utf-8") as f:
        PDF_CHUNKS = json.load(f)

# ── TF-IDF for PDF search ──
def tokenize(text):
    return re.findall(r'[a-záàâãéèêíïóôõúüç]{3,}', text.lower())

DOC_TOKENS = [tokenize(c["text"]) for c in PDF_CHUNKS]
DOC_COUNT = len(PDF_CHUNKS)

def idf(term):
    df = sum(1 for tokens in DOC_TOKENS if term in set(tokens))
    return math.log((DOC_COUNT + 1) / (df + 1)) + 1 if DOC_COUNT > 0 else 0

def search_pdf_chunks(query, top_k=3):
    if not PDF_CHUNKS:
        return []
    qtokens = tokenize(query)
    if not qtokens:
        return []
    scores = []
    for i, chunk in enumerate(PDF_CHUNKS):
        dt = DOC_TOKENS[i]
        if not dt:
            scores.append(0)
            continue
        score = sum((dt.count(qt) / len(dt)) * idf(qt) for qt in qtokens)
        scores.append(score)
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return [PDF_CHUNKS[i] for i in ranked[:top_k] if scores[i] > 0]

# ── Normalize text ──
def normalize(text):
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.category(c).startswith("M")).upper().strip()

# ── Municipality search ──
def search_municipios(query):
    query_upper = query.upper()
    query_norm = normalize(query)
    results = []
    flags = {}  # metadata flags for the LLM

    # 1. Check for IBGE code
    for code in re.findall(r"\b\d{6,7}\b", query):
        code_int = int(code)
        if code_int in MUN_BY_IBGE:
            results.append(MUN_BY_IBGE[code_int])

    # 2. Detect UF (sigla or nome)
    found_uf = None
    for uf in UFS:
        if re.search(rf"\b{uf}\b", query_upper):
            found_uf = uf
            break
    if not found_uf:
        for nome_estado, sigla in UF_NOMES.items():
            if nome_estado in query_norm:
                found_uf = sigla
                break

    # 3. Check for "planilha" / file sharing request
    if any(w in query_norm for w in ["PLANILHA", "ARQUIVO", "EXCEL", "BAIXAR", "DOWNLOAD"]):
        flags["pediu_arquivo"] = True

    # 4. Check for Brasil/total
    is_brasil = any(w in query_upper for w in ["BRASIL", "PAÍS", "NACIONAL"])
    is_total = "TOTAL" in query_upper

    # 5. Search municipality by name (if no IBGE match yet)
    if not results:
        scored = []
        query_words = [re.sub(r"[^A-Z]", "", w) for w in query_norm.split()]
        for nome, muns in MUN_BY_NOME.items():
            nome_norm = normalize(nome)
            if nome_norm == query_norm or nome_norm in query_norm:
                scored.append((100, nome, muns))
                continue
            if nome in query_upper:
                scored.append((90, nome, muns))
                continue
            nome_words = [w for w in nome_norm.split() if len(w) > 3]
            if nome_words:
                matches = sum(1 for w in nome_words if w in query_words)
                if matches == len(nome_words):
                    scored.append((80, nome, muns))
                elif matches > 0 and matches >= len(nome_words) * 0.5:
                    scored.append((50 + matches * 10, nome, muns))
        scored.sort(key=lambda x: -x[0])

        for score, nome, muns in scored[:5]:
            nome_norm = normalize(nome)
            # Check if this municipality is duplicated across UFs
            if nome_norm in MUN_DUPLICADOS:
                if found_uf:
                    # Filter to the correct UF
                    uf_muns = [m for m in muns if m["uf"] == found_uf]
                    if uf_muns:
                        results.extend(uf_muns[:1])
                    # else: UF specified but doesn't match — skip
                else:
                    # Ambiguous! Flag it so the LLM asks which UF
                    all_ufs = list(set(m["uf"] for m in muns))
                    flags["municipio_duplicado"] = {
                        "nome": nome,
                        "ufs": all_ufs,
                        "qtd_ufs": MUN_DUPLICADOS[nome_norm]
                    }
                    # Return all matches so LLM can list them
                    results.extend(muns)
            else:
                if found_uf:
                    uf_muns = [m for m in muns if m["uf"] == found_uf]
                    results.extend(uf_muns[:1] if uf_muns else muns[:1])
                else:
                    results.extend(muns[:1])

    # 6. UF totals (from Total_uf_brasil.xlsx)
    if found_uf and not results:
        if found_uf in TOTAL_BY_UF:
            t = TOTAL_BY_UF[found_uf]
            t["_tipo"] = "total_uf"
            results.append(t)

    # 7. Brasil total
    if (is_brasil or (is_total and not found_uf and not results)) and "Total Brasil" in TOTAL_BY_UF:
        t = TOTAL_BY_UF["Total Brasil"].copy()
        t["_tipo"] = "total_brasil"
        results.append(t)

    # 8. If "total" mentioned but ambiguous (municipality in conversation?)
    if is_total and not is_brasil and not found_uf and not results:
        flags["total_ambiguo"] = True

    # 9. If no municipality found at all by name
    if not results and not flags and not is_total and not is_brasil:
        # Check if query looks like it's asking about a municipality
        query_clean = re.sub(r"[^A-Z ]", "", query_norm)
        words = [w for w in query_clean.split() if len(w) > 3]
        if words:
            flags["municipio_nao_encontrado"] = True

    return results[:10], flags

# ── DeepSeek API ──
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

SYSTEM_PROMPT = """Você é o assistente do Novo PAC Saúde — Combo de Equipamentos UBS, do Ministério da Saúde do Brasil.
Responda perguntas sobre a distribuição de equipamentos para UBS nos municípios brasileiros.

REGRAS IMPORTANTES:
1. Responda APENAS com base nos dados do contexto. Se não encontrar, diga claramente.
2. Seja objetivo e direto. Use tabelas quando apropriado.
3. NÃO invente dados. Equipamento com valor 0 = município não recebeu aquele equipamento.
4. Quando houver info dos documentos (PDFs/FAQ), cite a fonte.
5. Responda sempre em português brasileiro.

REGRAS ESPECIAIS:
6. Se o usuário pedir para baixar planilha, arquivo ou dados: informe que NÃO é possível compartilhar arquivos, mas que você pode ajudá-lo com as informações via chat.
7. Se o campo "municipio_duplicado" aparecer nos flags, significa que existe mais de um município com esse nome em UFs diferentes. PERGUNTE ao usuário de qual estado (UF) ele está se referindo antes de responder.
8. Se "municipio_nao_encontrado" aparecer nos flags, informe que não encontrou o município pelo nome e PERGUNTE se o usuário possui o código IBGE do município para fazer a pesquisa.
9. Se o usuário perguntar sobre um equipamento que NÃO está na lista dos 17, informe que esse equipamento não faz parte do combo e liste os 17 equipamentos disponíveis para que ele identifique o correto. Os 17 são: 1.Balança Digital, 2.Cadeira de Rodas, 3.Câmara Fria para conservação de vacinas, 4.Dermatoscópio Digital, 5.Dinamômetro Digital, 6.Desfibrilador Externo Automático (DEA), 7.Doppler Vascular, 8.Eletrocardiógrafo Digital, 9.Eletrocautério (Bisturi Elétrico), 10.Espirômetro Digital, 11.Fotóforo Clínico, 12.Laser Terapêutico de Baixa Potência, 13.Otoscópio, 14.Retinógrafo Portátil, 15.TENS e FES, 16.Tábua de Propriocepção, 17.Ultrassom para Fisioterapia.
10. Se "total_ambiguo" aparecer nos flags, o usuário perguntou sobre "total" sem especificar se é de um município, UF ou Brasil. CONFIRME com ele: "Você gostaria do total de qual município, de algum estado específico ou do Brasil inteiro?"
11. Quando os dados vierem de "total_uf" ou "total_brasil", use esses valores diretamente — eles são os dados oficiais consolidados.
12. Mantenha o contexto da conversa: se o usuário fez uma pergunta sobre Brasília e depois pergunta "e quais equipamentos?", entenda que se refere a Brasília.
"""

@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    question = body.get("message", "")
    history = body.get("history", [])

    # Search using current question + recent context
    search_text = question
    if history:
        recent_user_msgs = [h["content"] for h in history if h["role"] == "user"][-2:]
        search_text = " ".join(recent_user_msgs) + " " + question

    mun_results, flags = search_municipios(search_text)
    pdf_results = search_pdf_chunks(search_text)

    # Fallback: try just the question
    if not mun_results and not flags:
        mun_results2, flags2 = search_municipios(question)
        if mun_results2 or flags2:
            mun_results, flags = mun_results2, flags2

    # Build context
    parts = []
    if flags:
        parts.append(f"=== FLAGS DE CONTEXTO ===\n{json.dumps(flags, ensure_ascii=False)}")
    if mun_results:
        parts.append("=== DADOS ===")
        for r in mun_results:
            parts.append(json.dumps(r, ensure_ascii=False, indent=2))
    if pdf_results:
        parts.append("\n=== DOCUMENTOS OFICIAIS / FAQ ===")
        for c in pdf_results:
            parts.append(f"[Fonte: {c['source']}, Página {c['page']}]\n{c['text']}")
    context = "\n---\n".join(parts) if parts else "Nenhum dado encontrado."

    # Build messages with history
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in history[-6:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": f"DADOS:\n{context}\n\nPERGUNTA:\n{question}"})

    if not DEEPSEEK_API_KEY:
        return JSONResponse({"error": "DEEPSEEK_API_KEY não configurada."}, status_code=500)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": messages, "max_tokens": 2000, "temperature": 0.3})

    if resp.status_code != 200:
        return JSONResponse({"error": f"Erro DeepSeek: {resp.text}"}, status_code=502)

    data = resp.json()
    answer = data["choices"][0]["message"]["content"]
    sources = [r.get("municipio", r.get("uf", "Brasil")) for r in mun_results if isinstance(r, dict)]
    sources += [f"{c['source']} (p.{c['page']})" for c in pdf_results]
    return {"answer": answer, "sources": sources}

@app.get("/")
async def index():
    with open(os.path.join(BASE_DIR, "static", "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())
