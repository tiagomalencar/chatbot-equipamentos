import os, json, re, math, unicodedata
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
BASE_DIR = os.path.dirname(__file__)

# ── Load data ──
with open(os.path.join(BASE_DIR, "municipios_data.json"), "r", encoding="utf-8") as f:
    MUNICIPIOS = json.load(f)
with open(os.path.join(BASE_DIR, "mun_duplicados.json"), "r", encoding="utf-8") as f:
    MUN_DUPLICADOS = json.load(f)
with open(os.path.join(BASE_DIR, "total_uf_brasil.json"), "r", encoding="utf-8") as f:
    TOTAL_UF_BRASIL = json.load(f)

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
TOTAL_BY_UF = {t["uf"]: t for t in TOTAL_UF_BRASIL}

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

# ── Normalize ──
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

    # UF detection
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

    is_brasil = any(w in query_upper for w in ["BRASIL","PAÍS","NACIONAL"])
    is_total = "TOTAL" in query_upper

    # If asking about Brasil total, skip municipality search
    if is_brasil and "Total Brasil" in TOTAL_BY_UF:
        t = TOTAL_BY_UF["Total Brasil"].copy(); t["_tipo"]="total_brasil"; results.append(t)
        return results[:10], flags

    # If asking about UF total (no municipality found by IBGE), return UF data
    if found_uf and not results:
        if found_uf in TOTAL_BY_UF:
            t = TOTAL_BY_UF[found_uf].copy(); t["_tipo"]="total_uf"; results.append(t)
            return results[:10], flags

    # Search by name
    if not results:
        scored = []
        query_words = [re.sub(r"[^A-Z]","",w) for w in query_norm.split()]
        # Words that are common in questions and should NOT trigger municipality matches
        STOP_WORDS = {
            "SAUDE","EQUIPAMENTO","EQUIPAMENTOS","COMBO","COMBOS","TOTAL","QUANTOS",
            "QUAIS","QUANTO","MUNICIPAL","MUNICIPIO","MUNICIPIOS","ESTADO","ESTADOS",
            "ENTREGA","ENTREGAS","DISTRIBUIDO","DISTRIBUIDOS","RECEBER","RECEBEU",
            "NOVO","NOVA","CAMPO","SANTA","SANTO","SERRA","BARRA","LAGOA","MORRO",
            "PORTO","PRAIA","PONTE","VISTA","VERDE","BRANCA","PRETA","GRANDE",
            "NORTE","LESTE","OESTE","CENTRO","ALTO","BAIXO","VILA","BELO","BELA",
            "AGUA","PEDRA","TERRA","MATA","VALE","BOAS","PRATICAS","COMO","PARA",
            "SOBRE","QUAL","ONDE","QUANDO","PODE","DEVE","FAZER","USAR","LIMPAR",
            "FRIA","FRIO","CAMARA","DIGITAL","CONSERVAR","MANTER","CUIDAR","CUIDADOS",
            "BRASIL","PAIS","NACIONAL","PORTARIA","PROGRAMA","UNIDADE","BASICA",
        }
        # Check if user explicitly named a municipality ("município de X", "cidade de X")
        explicit_mun = re.search(r'MUNICIPIO\s+(?:DE\s+)?(\w+)', query_norm)
        explicit_name = explicit_mun.group(1) if explicit_mun else None
        for nome, muns in MUN_BY_NOME.items():
            nome_norm = normalize(nome)
            # Skip single-word municipalities that are common words (unless explicitly named)
            nome_words_all = nome_norm.split()
            if len(nome_words_all) == 1 and nome_words_all[0] in STOP_WORDS:
                if query_norm.strip() == nome_norm:
                    scored.append((100,nome,muns))
                elif explicit_name and nome_norm == explicit_name:
                    scored.append((95,nome,muns))
                continue
            if nome_norm == query_norm or nome_norm in query_norm:
                scored.append((100,nome,muns)); continue
            if nome in query_upper:
                scored.append((90,nome,muns)); continue
            nome_words = [w for w in nome_norm.split() if len(w) > 3 and w not in STOP_WORDS]
            if nome_words:
                matches = sum(1 for w in nome_words if w in query_words)
                if matches == len(nome_words):
                    scored.append((80,nome,muns))
                elif matches > 0 and matches >= len(nome_words)*0.5:
                    scored.append((50+matches*10,nome,muns))
        scored.sort(key=lambda x:-x[0])
        for score, nome, muns in scored[:5]:
            nome_norm = normalize(nome)
            if nome_norm in MUN_DUPLICADOS:
                if found_uf:
                    uf_muns = [m for m in muns if m["uf"]==found_uf]
                    if uf_muns: results.extend(uf_muns[:1])
                else:
                    all_ufs = list(set(m["uf"] for m in muns))
                    flags["municipio_duplicado"] = {"nome":nome,"ufs":all_ufs,"qtd_ufs":MUN_DUPLICADOS[nome_norm]}
                    results.extend(muns)
            else:
                if found_uf:
                    uf_muns = [m for m in muns if m["uf"]==found_uf]
                    results.extend(uf_muns[:1] if uf_muns else muns[:1])
                else:
                    results.extend(muns[:1])

    if is_total and not is_brasil and not found_uf and not results:
        flags["total_ambiguo"] = True

    # Generic quantity questions without municipality context
    qty_words = ["QUANTOS","QUANTAS","QUANTO","DISTRIBUIDO","DISTRIBUIDOS","RECEBER","RECEBEU","SERAO"]
    is_qty_question = any(w in query_upper for w in qty_words)
    if is_qty_question and not results and not found_uf and not is_brasil and not flags.get("total_ambiguo"):
        flags["pergunta_generica_sem_contexto"] = True

    if not results and not flags and not is_total and not is_brasil and not is_qty_question:
        query_clean = re.sub(r"[^A-Z ]","",query_norm)
        words = [w for w in query_clean.split() if len(w) > 3]
        if words: flags["municipio_nao_encontrado"] = True

    return results[:10], flags

# ── DeepSeek API ──
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY","")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

SYSTEM_PROMPT = """Você é o assistente do Novo PAC Saúde — Combo de Equipamentos UBS, do Ministério da Saúde do Brasil.
Responda perguntas sobre a distribuição de combos de equipamentos para UBS, boas práticas de uso e conservação dos equipamentos, e dúvidas gerais sobre o programa.

REGRAS OBRIGATÓRIAS:
1. Responda APENAS com base nos dados do contexto. Se não encontrar, diga claramente.
2. Seja objetivo e direto. Use tabelas quando apropriado.
3. NÃO invente dados.
4. Responda sempre em português brasileiro.
5. NUNCA referencie documentos, fontes, PDFs, FAQs ou números de página. Responda naturalmente como se soubesse a informação.
6. NUNCA informe quantidade por tipo de equipamento. Informe apenas a quantidade de COMBOS.
7. Quando perguntado "quantos equipamentos", responda com a quantidade de combos e informe que cada combo possui 17 equipamentos.
8. Pode listar quais são os 17 equipamentos do combo, mas NUNCA diga quantos de cada tipo o município vai receber.
9. Se o usuário pedir planilha, arquivo ou download: diga que não é possível compartilhar arquivos, mas que pode ajudar via chat.
10. Se "municipio_duplicado" nos flags: pergunte de qual estado (UF) antes de responder.
11. Se "municipio_nao_encontrado" nos flags: diga que não encontrou pelo nome e pergunte se tem o código IBGE.
12. Se "total_ambiguo" ou "pergunta_generica_sem_contexto" nos flags: pergunte ao usuário se deseja a informação do Brasil inteiro ou de algum município/estado específico. NÃO chute nenhum município.
13. Mantenha o contexto da conversa (se falou de Brasília e depois pergunta "e os equipamentos?", refere-se a Brasília).
14. Os 17 equipamentos DO COMBO são: 1.Balança portátil digital, 2.Cadeira de rodas, 3.Câmara fria para vacinas, 4.Dermatoscópio digital, 5.Dinamômetro digital, 6.DEA, 7.Doppler vascular portátil, 8.Eletrocardiógrafo digital, 9.Eletrocautério (bisturi elétrico), 10.Espirômetro digital, 11.Fotóforo clínico, 12.Laser terapêutico de baixa potência, 13.Otoscópio, 14.Retinógrafo portátil, 15.TENS e FES, 16.Tábua de propriocepção, 17.Ultrassom para fisioterapia.

SOBRE BOAS PRÁTICAS E GUIAS:
15. Você possui informações de boas práticas de uso, conservação, manutenção, indicações clínicas e cuidados para TODOS os equipamentos mencionados nos guias, incluindo itens que NÃO fazem parte do combo (como raios X portátil, ultrassom diagnóstico, etc.). Pode e deve responder perguntas sobre boas práticas, uso e conservação de QUALQUER equipamento coberto nos guias.
16. A restrição de NÃO informar quantidades por tipo se aplica APENAS à distribuição de combos. Para perguntas sobre uso, conservação, indicações, manutenção e boas práticas, responda normalmente com toda a informação disponível.
17. Se o usuário perguntar sobre a QUANTIDADE de um equipamento que não está na lista dos 17 do combo, informe que esse equipamento não faz parte do combo e liste os 17 para que ele identifique o correto. Mas se a pergunta for sobre USO, CONSERVAÇÃO ou BOAS PRÁTICAS de qualquer equipamento, responda normalmente.
"""

@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    question = body.get("message","")
    history = body.get("history",[])

    # Search question alone first
    mun_results, flags = search_municipios(question)
    pdf_results = search_pdf_chunks(question)

    # If no municipality found and we have history, try with context
    if not mun_results and not flags.get("pergunta_generica_sem_contexto") and history:
        recent = [h["content"] for h in history if h["role"]=="user"][-2:]
        search_text = " ".join(recent) + " " + question
        mun_results2, flags2 = search_municipios(search_text)
        if mun_results2: mun_results, flags = mun_results2, flags2
        # Also search PDFs with broader context
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
    return {"answer":answer}

@app.get("/")
async def index():
    with open(os.path.join(BASE_DIR,"static","index.html"),"r",encoding="utf-8") as f:
        return HTMLResponse(f.read())
