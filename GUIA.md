# 🚀 Guia Passo a Passo — Chatbot Equipamentos UBS

## O que você vai ter no final
Um chatbot online, com URL própria, que responde perguntas sobre equipamentos do Novo PAC Saúde usando IA (DeepSeek). Qualquer pessoa com o link pode acessar.

---

## ETAPA 1 — Criar contas (10 minutos)

### 1.1 — Conta no GitHub
O GitHub é onde o código do seu projeto fica armazenado.

1. Acesse https://github.com
2. Clique em **Sign up**
3. Crie uma conta com seu e-mail
4. Confirme o e-mail

### 1.2 — Conta no Render
O Render é o servidor que vai rodar seu chatbot online (gratuito).

1. Acesse https://render.com
2. Clique em **Get Started for Free**
3. Escolha **Sign in with GitHub** (usa a conta que acabou de criar)
4. Autorize o acesso

### 1.3 — Chave da API DeepSeek
O DeepSeek é a IA que gera as respostas do chatbot.

1. Acesse https://platform.deepseek.com
2. Crie uma conta
3. Adicione crédito (o mínimo, ~US$2-5 — vai durar MESES)
4. Vá em **API Keys** → **Create API Key**
5. COPIE e GUARDE essa chave em um lugar seguro (ex: bloco de notas). Ela começa com `sk-...`

---

## ETAPA 2 — Instalar Git no seu computador (5 minutos)

### Windows
1. Acesse https://git-scm.com/download/win
2. Baixe e instale com as opções padrão (Next, Next, Next...)
3. Após instalar, abra o **Prompt de Comando** (tecle `Win+R`, digite `cmd`, Enter)
4. Digite: `git --version` — deve aparecer algo como `git version 2.x.x`

### Mac
1. Abra o **Terminal** (Cmd+Espaço, digite Terminal)
2. Digite: `git --version`
3. Se não estiver instalado, o Mac vai perguntar se quer instalar — aceite

---

## ETAPA 3 — Subir o projeto para o GitHub (10 minutos)

### 3.1 — Criar o repositório no GitHub
1. Vá em https://github.com → clique no **+** no canto superior direito → **New repository**
2. Nome: `chatbot-equipamentos`
3. Deixe **Public**
4. NÃO marque "Add a README"
5. Clique em **Create repository**
6. Vai aparecer uma página com instruções — DEIXE ESSA PÁGINA ABERTA

### 3.2 — Baixar os arquivos do projeto
Você recebeu um arquivo ZIP com o projeto completo. Extraia ele em uma pasta no seu computador, por exemplo:
- Windows: `C:\Users\SeuNome\chatbot-equipamentos`
- Mac: `/Users/SeuNome/chatbot-equipamentos`

A pasta deve conter:
```
chatbot-equipamentos/
├── main.py
├── requirements.txt
├── render.yaml
├── municipios_data.json
└── static/
    └── index.html
```

### 3.3 — Enviar para o GitHub
Abra o **Prompt de Comando** (Windows) ou **Terminal** (Mac) e digite os comandos abaixo, UM POR UM:

```bash
cd C:\Users\SeuNome\chatbot-equipamentos
```
(troque pelo caminho real da sua pasta)

```bash
git init
git add .
git commit -m "primeiro commit"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/chatbot-equipamentos.git
git push -u origin main
```

⚠️ Troque `SEU_USUARIO` pelo seu nome de usuário do GitHub.

Se pedir login, use seu usuário e senha do GitHub (ou token — o GitHub pode pedir para criar um token em Settings → Developer Settings → Personal Access Tokens → Generate new token, marque "repo" e use o token como senha).

Depois de fazer o push, atualize a página do GitHub — seus arquivos devem aparecer lá.

---

## ETAPA 4 — Fazer o Deploy no Render (5 minutos)

1. Acesse https://dashboard.render.com
2. Clique em **New** → **Web Service**
3. Selecione **Build and deploy from a Git repository** → Next
4. Conecte seu repositório `chatbot-equipamentos` do GitHub
5. Preencha:
   - **Name**: `chatbot-equipamentos`
   - **Region**: Oregon (US West) ou qualquer uma
   - **Branch**: `main`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
6. Escolha o plano **Free**
7. Em **Environment Variables**, clique em **Add Environment Variable**:
   - **Key**: `DEEPSEEK_API_KEY`
   - **Value**: cole a chave que você salvou na Etapa 1.3 (a que começa com `sk-...`)
8. Clique em **Create Web Service**

### Aguarde o deploy
O Render vai instalar as dependências e iniciar o servidor. Isso leva ~2-3 minutos.
Quando aparecer **"Your service is live"**, clique na URL que aparece no topo (algo como `https://chatbot-equipamentos.onrender.com`).

🎉 **Seu chatbot está online!**

---

## ETAPA 5 — Testar

Acesse a URL do Render e teste perguntas como:
- "Quantos combos o município de Brasília recebeu?"
- "Quais equipamentos foram entregues em Ariquemes?"
- "Resumo de equipamentos do estado de SP"
- "Total de equipamentos distribuídos no Brasil"

---

## ETAPA 6 — Incorporar na landing page do Ministério (depois)

Quando quiser colocar o chat dentro da landing page do Novo PAC Saúde, basta adicionar um iframe:

```html
<iframe 
  src="https://chatbot-equipamentos.onrender.com" 
  width="100%" 
  height="600" 
  style="border:none; border-radius:12px;"
></iframe>
```

Ou, se preferir um botão flutuante que abre o chat, podemos criar uma versão widget depois.

---

## Problemas comuns

| Problema | Solução |
|----------|---------|
| "DEEPSEEK_API_KEY não configurada" | Verifique se adicionou a variável de ambiente no Render |
| O site demora para abrir | No plano free do Render, o servidor "dorme" após inatividade. A primeira requisição leva ~30s |
| Erro de push no GitHub | Verifique se o `remote` está correto: `git remote -v` |
| Respostas imprecisas | A busca de município é por nome — use o nome exato ou código IBGE |

---

## Custos

| Serviço | Custo |
|---------|-------|
| GitHub | Gratuito |
| Render (free tier) | Gratuito |
| DeepSeek API | ~R$0,01 por pergunta (US$2 duram meses) |
| **Total mensal** | **~R$0 a R$10** |

---

## Próximos passos (melhorias futuras)

1. **Adicionar PDFs (manuais)**: implementar RAG com embeddings para buscar nos manuais de equipamentos
2. **Domínio personalizado**: conectar um domínio `.gov.br` ou próprio ao Render
3. **Widget flutuante**: versão pop-up para embutir no site do Ministério
4. **Cache de respostas**: reduzir custo com perguntas repetidas
5. **Painel admin**: dashboard com métricas de uso
