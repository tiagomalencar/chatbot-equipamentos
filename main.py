import os, json, re, math, unicodedata
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import httpx

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
BASE_DIR = os.path.dirname(__file__)
LOG_FILE = os.path.join(BASE_DIR, "chat_logs.jsonl")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

def log_conversation(question, answer, ip=""):
    try:
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "question": question,
            "answer": answer[:500],
            "ip": ip,
        }
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except:
        pass

# ── Load data ──
with open(os.path.join(BASE_DIR, "municipios_data.json"), "r", encoding="utf-8") as f:
    MUNICIPIOS = json.load(f)
with open(os.path.join(BASE_DIR, "mun_duplicados.json"), "r", encoding="utf-8") as f:
    MUN_DUPLICADOS = json.load(f)

MUN_BY_IBGE = {m["ibge"]: m for m in MUNICIPIOS}
MUN_BY_NOME = {}
for m in MUNICIPIOS:
    MUN_BY_NOME.setdefault(m["municipio"].upper().strip(), []).append(m)

UFS = list(set(m["uf"] for m in MUNICIPIOS))
UF_NOMES = {
    "ACRE":"AC","ALAGOAS":"AL","AMAPA":"AP","AMAZONAS":"AM","BAHIA":"BA","CEARA":"CE",
    "DISTRITO FEDERAL":"DF","ESPIRITO SANTO":"ES","GOIAS":"GO","MARANHAO":"MA",
    "MATO GROSSO":"MT","MATO GROSSO DO SUL":"MS","MINAS GERAIS":"MG","PARA":"PA",
    "PARAIBA":"PB","PARANA":"PR","PERNAMBUCO":"PE","PIAUI":"PI","RIO DE JANEIRO":"RJ",
    "RIO GRANDE DO NORTE":"RN","RIO GRANDE DO SUL":"RS","RONDONIA":"RO","RORAIMA":"RR",
    "SANTA CATARINA":"SC","SAO PAULO":"SP","SERGIPE":"SE","TOCANTINS":"TO",
}

# ── PDF/FAQ chunks + TF-IDF ──
PDF_CHUNKS = []
chunks_path = os.path.join(BASE_DIR, "pdf_chunks.json")
if os.path.exists(chunks_path):
    with open(chunks_path, "r", encoding="utf-8") as f:
        PDF_CHUNKS = json.load(f)

def tokenize(text):
    return re.findall(r'[a-záàâãéèêíïóôõúüç]{3,}', text.lower())

DOC_TOKENS = [tokenize(c["text"]) for c in PDF_CHUNKS]
DOC_COUNT = len(PDF_CHUNKS)

def idf(term):
    df = sum(1 for tokens in DOC_TOKENS if term in set(tokens))
    return math.log((DOC_COUNT + 1) / (df + 1)) + 1 if DOC_COUNT > 0 else 0

def search_pdf_chunks(query, top_k=4):
    if not PDF_CHUNKS: return []
    qtokens = tokenize(query)
    if not qtokens: return []
    scores = []
    for i, chunk in enumerate(PDF_CHUNKS):
        dt = DOC_TOKENS[i]
        if not dt: scores.append(0); continue
        score = sum((dt.count(qt) / len(dt)) * idf(qt) for qt in qtokens)
        scores.append(score)
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return [PDF_CHUNKS[i] for i in ranked[:top_k] if scores[i] > 0]

def normalize(text):
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.category(c).startswith("M")).upper().strip()

# ── Municipality search ──
def search_municipios(query):
    query_upper = query.upper()
    query_norm = normalize(query)
    results = []
    flags = {}

    # IBGE code
    for code in re.findall(r"\b\d{6,7}\b", query):
        code_int = int(code)
        if code_int in MUN_BY_IBGE:
            results.append(MUN_BY_IBGE[code_int])

    # UF detection (sigla or nome completo)
    found_uf = None
    for uf in UFS:
        if re.search(rf"\b{uf}\b", query_upper):
            found_uf = uf; break
    if not found_uf:
        for nome_estado, sigla in UF_NOMES.items():
            if nome_estado in query_norm:
                found_uf = sigla; break

    # File sharing request
    if any(w in query_norm for w in ["PLANILHA","ARQUIVO","EXCEL","BAIXAR","DOWNLOAD"]):
        flags["pediu_arquivo"] = True

    # Search by name
    if not results:
        scored = []
        query_words = [re.sub(r"[^A-Z]","",w) for w in query_norm.split()]
        for nome, muns in MUN_BY_NOME.items():
            nome_norm = normalize(nome)
            if nome_norm == query_norm or nome_norm in query_norm:
                scored.append((100,nome,muns)); continue
            if nome in query_upper:
                scored.append((90,nome,muns)); continue
            nome_words = [w for w in nome_norm.split() if len(w) > 3]
            if nome_words:
                matches = sum(1 for w in nome_words if w in query_words)
                if matches == len(nome_words):
                    scored.append((80,nome,muns))
        scored.sort(key=lambda x:-x[0])
        for score, nome, muns in scored[:5]:
            nome_norm = normalize(nome)
            if nome_norm in MUN_DUPLICADOS:
                if found_uf:
                    uf_muns = [m for m in muns if m["uf"]==found_uf]
                    if uf_muns: results.extend(uf_muns[:1])
                else:
                    all_ufs = sorted(set(m["uf"] for m in muns))
                    flags["municipio_duplicado"] = {"nome":nome,"ufs":all_ufs}
                    results.extend(muns)
            else:
                if found_uf:
                    uf_muns = [m for m in muns if m["uf"]==found_uf]
                    results.extend(uf_muns[:1] if uf_muns else muns[:1])
                else:
                    results.extend(muns[:1])

    # If nothing found and it looks like a municipality question
    if not results and not flags:
        # Check if it's a generic quantity question (no municipality mentioned)
        qty_words = ["QUANTOS","QUANTAS","QUANTO","DISTRIBUIDO","DISTRIBUIDOS","RECEBER","RECEBEU","SERAO"]
        is_qty = any(w in query_upper for w in qty_words)
        if is_qty:
            flags["pergunta_generica_sem_contexto"] = True
        else:
            query_clean = re.sub(r"[^A-Z ]","",query_norm)
            words = [w for w in query_clean.split() if len(w) > 3]
            if words and not any(w in query_norm for w in ["BOAS","PRATICAS","COMO","CONSERVAR","LIMPAR","CUIDADOS","EQUIPAMENTO","COMBO","PROGRAMA","CAPACITACAO","GARANTIA","MANUTENCAO","REGISTRO","RECEBIMENTO"]):
                flags["municipio_nao_encontrado"] = True

    return results[:10], flags

# ── DeepSeek ──
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY","")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

SYSTEM_PROMPT = """Você é o assistente do Novo PAC Saúde — Combo de Equipamentos UBS, do Ministério da Saúde do Brasil.
Responda perguntas sobre a distribuição de combos de equipamentos para UBS, boas práticas de uso e conservação dos equipamentos, e dúvidas gerais sobre o programa.

REGRAS OBRIGATÓRIAS:
1. Responda APENAS com base nos dados do contexto. Se não encontrar, diga claramente.
2. Seja objetivo e direto.
3. NÃO invente dados.
4. Responda sempre em português brasileiro.
5. NUNCA referencie documentos, fontes, PDFs, FAQs ou números de página. Responda naturalmente.
6. NUNCA faça contas com quantidades de equipamentos. Informe apenas a quantidade de COMBOS que o município vai receber.
7. Quando perguntado "quantos equipamentos", responda com a quantidade de combos e explique que cada combo é composto por 17 equipamentos (liste-os caso ainda não tenha feito na conversa).
8. NUNCA informe quantidade por tipo de equipamento.
9. NUNCA assuma ou deduza que um município específico receberá o 18º equipamento (ultrassom portátil de bolso). Você NÃO tem essa informação por município. Se perguntado, diga que essa informação depende de critérios específicos (UBS cenário de residência em MFC ou UBS fluvial) e que o município deve consultar os canais oficiais.
9. Se o usuário pedir planilha/arquivo/download: diga que não pode compartilhar arquivos, mas pode ajudar via chat.
10. Se "municipio_duplicado" nos flags: informe que existem municípios com esse nome em mais de uma UF e PERGUNTE de qual estado, listando as opções.
11. Se o usuário responder apenas o nome de um estado (ex: "Maranhão"), ASSOCIE à pergunta anterior sobre o município e busque o município naquela UF.
12. Se "municipio_nao_encontrado" nos flags: diga que não encontrou pelo nome e pergunte se tem o código IBGE.
13. Se "pergunta_generica_sem_contexto" nos flags: pergunte ao usuário a qual município se refere, pois as informações de quantidade estão disponíveis por município.
14. Mantenha o contexto da conversa. Se falou de Brasília e depois pergunta "e quantos combos?", refere-se a Brasília.
15. Os 17 equipamentos do combo são: 1.Balança portátil digital, 2.Cadeira de rodas, 3.Câmara fria para vacinas, 4.Dermatoscópio digital, 5.Dinamômetro digital, 6.DEA, 7.Doppler vascular portátil, 8.Eletrocardiógrafo digital, 9.Eletrocautério (bisturi elétrico), 10.Espirômetro digital, 11.Fotóforo clínico, 12.Laser terapêutico de baixa potência, 13.Otoscópio, 14.Retinógrafo portátil, 15.TENS e FES, 16.Tábua de propriocepção, 17.Ultrassom para fisioterapia.

SOBRE BOAS PRÁTICAS:
16. Você possui informações sobre boas práticas de uso, conservação, manutenção e indicações de equipamentos. Pode e deve responder essas perguntas normalmente.
17. A restrição de NÃO informar quantidades se aplica APENAS à distribuição. Para uso, conservação e boas práticas, responda com toda informação disponível.
"""

@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    question = body.get("message","")
    history = body.get("history",[])

    # Detect if user is answering with just a state name (response to "which UF?" question)
    question_norm = normalize(question)
    is_uf_response = False
    detected_uf = None

    # Check if question is just a UF sigla
    if question.strip().upper() in UFS:
        detected_uf = question.strip().upper()
        is_uf_response = True
    # Check if question is just a state name
    for nome_estado, sigla in UF_NOMES.items():
        if nome_estado == question_norm or question_norm == nome_estado:
            detected_uf = sigla
            is_uf_response = True
            break

    # If user just answered with a UF, find the municipality from previous messages
    if is_uf_response and history:
        # Look for the municipality name in recent user messages
        for h in reversed(history):
            if h["role"] == "user":
                prev_results, prev_flags = search_municipios(h["content"])
                if prev_results:
                    # Filter by the UF the user just specified
                    for r in prev_results:
                        if r.get("uf") == detected_uf:
                            mun_results = [r]
                            flags = {}
                            pdf_results = search_pdf_chunks(h["content"] + " " + question)
                            # Build context and call LLM
                            parts = []
                            if mun_results:
                                parts.append("=== DADOS ===")
                                for r2 in mun_results: parts.append(json.dumps(r2, ensure_ascii=False, indent=2))
                            if pdf_results:
                                parts.append("\n=== INFORMAÇÕES COMPLEMENTARES ===")
                                for c in pdf_results: parts.append(c['text'])
                            context = "\n---\n".join(parts) if parts else "Nenhum dado encontrado."
                            messages = [{"role":"system","content":SYSTEM_PROMPT}]
                            for hh in history[-6:]: messages.append({"role":hh["role"],"content":hh["content"]})
                            messages.append({"role":"user","content":f"DADOS:\n{context}\n\nPERGUNTA:\n{question}"})
                            return await _call_and_log(messages, question, request)
                break

    # Normal flow: search question alone first
    mun_results, flags = search_municipios(question)
    pdf_results = search_pdf_chunks(question)

    # If no municipality found, try with conversation context
    if not mun_results and history:
        recent = [h["content"] for h in history if h["role"]=="user"][-2:]
        search_text = " ".join(recent) + " " + question
        mun_results2, flags2 = search_municipios(search_text)
        if mun_results2: mun_results, flags = mun_results2, flags2
        pdf_results2 = search_pdf_chunks(search_text)
        if pdf_results2 and not pdf_results: pdf_results = pdf_results2

    parts = []
    if flags: parts.append(f"=== FLAGS ===\n{json.dumps(flags, ensure_ascii=False)}")
    if mun_results:
        parts.append("=== DADOS ===")
        for r in mun_results: parts.append(json.dumps(r, ensure_ascii=False, indent=2))
    if pdf_results:
        parts.append("\n=== INFORMAÇÕES COMPLEMENTARES ===")
        for c in pdf_results: parts.append(c['text'])
    context = "\n---\n".join(parts) if parts else "Nenhum dado encontrado."

    messages = [{"role":"system","content":SYSTEM_PROMPT}]
    for h in history[-6:]: messages.append({"role":h["role"],"content":h["content"]})
    messages.append({"role":"user","content":f"DADOS:\n{context}\n\nPERGUNTA:\n{question}"})

    return await _call_and_log(messages, question, request)


async def _call_and_log(messages, question, request):
    ip = request.client.host if request.client else ""
    if not DEEPSEEK_API_KEY:
        return JSONResponse({"error":"DEEPSEEK_API_KEY não configurada."},status_code=500)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(DEEPSEEK_URL,
            headers={"Authorization":f"Bearer {DEEPSEEK_API_KEY}","Content-Type":"application/json"},
            json={"model":"deepseek-chat","messages":messages,"max_tokens":2000,"temperature":0.3})

    if resp.status_code != 200:
        return JSONResponse({"error":f"Erro DeepSeek: {resp.text}"},status_code=502)

    data = resp.json()
    answer = data["choices"][0]["message"]["content"]
    log_conversation(question, answer, ip)
    return {"answer":answer}


LOGS_PASSWORD = os.environ.get("LOGS_PASSWORD", "combo2026")

@app.get("/api/logs")
async def get_logs(senha: str = "", limit: int = 100):
    if senha != LOGS_PASSWORD:
        return JSONResponse({"error": "Acesso negado. Use ?senha=SUA_SENHA"}, status_code=401)
    if not os.path.exists(LOG_FILE):
        return {"logs": [], "total": 0}
    logs = []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try: logs.append(json.loads(line))
                except: pass
    logs.reverse()
    return {"logs": logs[:limit], "total": len(logs)}

@app.get("/")
async def index():
    with open(os.path.join(BASE_DIR,"static","index.html"),"r",encoding="utf-8") as f:
        return HTMLResponse(f.read())
