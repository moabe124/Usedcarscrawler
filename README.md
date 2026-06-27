# Usedcarscrawler

Crawler de preços de carros usados da OLX (estado de PE) com uma interface web em
Python pra visualizar os anúncios num gráfico de preço × ano.

**Motivação:** queria comprar um carro, mas a visualização da OLX/Mercado Livre não
ajudava a comparar preços. Esse projeto raspa os anúncios, salva no MongoDB e mostra
cada carro como um ponto num gráfico — fica fácil achar o mais barato.

## Componentes

- **`updateDatabase.py`** — crawler: roda **OLX e Webmotors em paralelo** (uma thread
  por fonte), em loop, e grava tudo na mesma coleção do MongoDB. Cada fonte tem sua
  própria cadência e tolera falha da outra. Desligue qualquer uma com
  `ENABLE_OLX=0` / `ENABLE_WEBMOTORS=0`.
- **`utils/crawlerCore.py`** — coleta da **OLX** (Cloudflare; Selenium stealth headless).
- **`utils/webmotorsCore.py`** — coleta da **Webmotors**. O site fica atrás do
  **PerimeterX**, que bloqueia Selenium comum; por isso usa **undetected-chromedriver**
  e **precisa rodar com janela visível** (headless é barrado — veja abaixo). Extrai
  tudo da **listagem** (nunca abre o anúncio), parseando o DOM renderizado. O id único
  do anúncio (final da URL) é gravado como `wm-<id>`, então nunca colide com os ids da
  OLX. Cada registro leva `source: "olx"` / `source: "webmotors"`.
- **`app.py`** — app Flask: serve a API JSON (`/api/cars`) e a página com o gráfico (`/`).
  A página tem uma **calculadora de financiamento** (Tabela Price): você configura
  juros (% a.m.) e entrada (R$), e cada carro mostra as parcelas em **48x e 60x**.
  O eixo Y do gráfico pode alternar entre preço total e valor da parcela. A tabela
  também traz um **score de custo-benefício** (ver seção abaixo). A página tem ainda
  um **dashboard**: cards de resumo (KPIs), o scatter com os pontos **coloridos pelo
  score** (verde = bom negócio) e um ranking de **modelos com mais achados**
  (anúncios abaixo dos pares), pra decidir qual modelo vale a pena caçar.
- **`utils/`** — núcleo do crawler (`crawlerCore.py`), o ranking de custo-benefício
  (`ranking.py`) e configurações (`constants.py`).

## Rodando com Docker (recomendado)

Sobe MongoDB + web + crawler de uma vez:

```bash
docker compose up --build
```

Depois acesse **http://localhost:5000**. O crawler começa a popular o banco em background.

## Banco grátis na nuvem (MongoDB Atlas)

Pra rodar sem Docker, o jeito mais simples é usar o **MongoDB Atlas** (tier free M0,
512 MB, sem cartão):

1. Crie uma conta em https://www.mongodb.com/cloud/atlas e um cluster **M0 (Free)**.
2. Em **Database Access**, crie um usuário/senha.
3. Em **Network Access**, libere seu IP (ou `0.0.0.0/0` para testar de qualquer lugar).
4. Em **Database > Connect > Drivers**, copie a connection string (`mongodb+srv://...`).
5. Copie `.env.example` para `.env` e cole a string em `MONGO_URI`.

O `.env` é carregado automaticamente (`python-dotenv`) e nunca é comitado.

## Rodando local (sem Docker)

Requisitos: **Python 3.12**, **Google Chrome** instalado e um **MongoDB** acessível
— use o Atlas (acima) ou um Mongo local em `localhost:27017`.

```bash
pip install -r requirements.txt

# 1) interface web (API + gráfico)
python app.py            # http://localhost:5000

# 2) crawler (em outro terminal, popula o banco)
python updateDatabase.py
```

> O Selenium 4.6+ baixa o chromedriver automaticamente (Selenium Manager) — não
> precisa mais baixar o `chromedriver.exe` manualmente.

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | Conexão com o MongoDB |
| `PAGE_LIMIT` | `10` | Quantas páginas o crawler percorre por ciclo normal |
| `PRICE_CEILING` | `300000` | Preço máximo (R$) considerado; acima disso é ignorado |
| `BACKFILL_THRESHOLD` | `200` | Abaixo deste nº de registros, entra em modo backfill |
| `BACKFILL_TARGET` | `500` | Meta de registros a atingir no modo backfill |
| `BACKFILL_MAX_PAGES` | `30` | Teto de páginas no backfill (evita loop infinito) |

> **Modo backfill:** quando o banco tem menos de `BACKFILL_THRESHOLD` registros, o
> crawler ignora a parada por anúncios repetidos e percorre páginas continuamente
> até alcançar `BACKFILL_TARGET` registros (ou esgotar as páginas). Útil pra popular
> o banco do zero. (A Webmotors tem seu próprio backfill, com as variáveis `WEBMOTORS_*`.)

### Variáveis da Webmotors

A Webmotors está atrás do **PerimeterX**, mais agressivo que o Cloudflare da OLX. Dois
pontos práticos: **roda com janela visível** (headless é bloqueado) e os **delays são
propositalmente folgados** pra não tomar ban de IP (há jitter aleatório de ±25%).

| Variável | Padrão | Descrição |
|---|---|---|
| `ENABLE_OLX` | `1` | Liga/desliga a coleta da OLX |
| `ENABLE_WEBMOTORS` | `1` | Liga/desliga a coleta da Webmotors |
| `WEBMOTORS_HEADLESS` | `0` | `1` roda sem janela (só funciona atrás de display virtual, ex.: xvfb) |
| `WEBMOTORS_ESTADOCIDADE` | `Pernambuco` | Região da busca (espelha o escopo PE da OLX) |
| `WEBMOTORS_PAGE_LIMIT` | `5` | Páginas por ciclo normal |
| `WEBMOTORS_PAGE_DELAY` | `150` | Segundos entre páginas (~2,5 min) |
| `WEBMOTORS_CYCLE_DELAY` | `900` | Segundos entre ciclos (~15 min) |
| `WEBMOTORS_BACKFILL_THRESHOLD` | `200` | Abaixo disso, entra em backfill |
| `WEBMOTORS_BACKFILL_TARGET` | `500` | Meta de registros no backfill |
| `WEBMOTORS_BACKFILL_MAX_PAGES` | `30` | Teto de páginas no backfill |
| `CHROME_MAJOR` | (auto) | Força a versão do chromedriver; só se a detecção automática falhar |

> **Por que janela visível?** O PerimeterX detecta o fingerprint headless na hora
> (a listagem volta "Access to this page has been denied"). Com janela visível, o
> undetected-chromedriver passa. Em servidor Linux, rode atrás de um `xvfb` e ligue
> `WEBMOTORS_HEADLESS=1`.

## Ranking de custo-benefício

A interface ranqueia os carros por um **score de 0 a 100** que estima o quão bom é
o negócio — usando **apenas os dados da listagem** (preço, ano, km, título). Não
depende de tabela FIPE nem do LLM.

**Como o score é calculado** (`utils/ranking.py`):

1. **Pares.** Cada carro é comparado com os *pares* dele já no banco: mesmo
   **modelo** (as 2 primeiras palavras do título, com apelidos de marca
   normalizados — `VW`→`Volkswagen` etc.) e **ano próximo**. A janela de ano abre
   de `±0` até `±3` anos até juntar pelo menos `MIN_PEERS` (3) pares. *Não* há
   fallback para "mesmo modelo, qualquer ano" — preço é dominado pela idade, então
   isso faria todo carro velho parecer barato. Sem pares suficientes, o carro fica
   **sem nota** (`—`) em vez de receber uma nota enganosa.
2. **Gap de preço** (peso 65%): preço vs. a **mediana** dos pares. Mais barato que
   os equivalentes = melhor.
3. **Coerência de km** (peso 35%): km vs. o esperado (`~12.000 km/ano` × idade).
   Rodado a menos = melhor.
4. **Flags**: `barato`/`caro`, `km baixo`/`km alto`, e alertas `preço suspeito`
   (bom demais, possível problema/golpe) e `km suspeito` (odômetro improvável).
   Anúncios com alerta são **rebaixados** (teto de 60) pra não liderarem o ranking,
   mas continuam visíveis com o aviso.

**Recálculo automático.** O score **não é gravado** no banco — é recomputado a cada
chamada de `/api/cars`, sempre a partir do estado atual da coleção. Conforme o
crawler insere novos anúncios, a próxima requisição já reflete os pares novos. Não
há job de recálculo a manter.

> **Limitação conhecida:** o "modelo" ignora a versão/acabamento (ex.: Onix *Joy*
> base vs. *Premier*). Dentro do mesmo modelo+ano, versões mais simples tendem a
> pontuar como "barato". Os parâmetros (`KM_PER_YEAR`, `MIN_PEERS`, `YEAR_WINDOW`,
> pesos, limiares de suspeita) ficam no topo de `utils/ranking.py` para ajuste.

## Avaliação por LLM local (opcional, em validação)

Um LLM rodando **localmente** lê a descrição do anúncio (da tela de detalhe) e
gera um resumo opinativo, um score de custo-benefício, tags, campos estruturados
(único dono, IPVA pago, aceita troca...) e red flags.

**Setup (recomendado para GPU AMD):**

1. Instale o **LM Studio** (https://lmstudio.ai).
2. Baixe o modelo **`qwen2.5-7b-instruct`** (Q4_K_M) e selecione o runtime
   **Vulkan** (usa a GPU AMD; ROCm pode não suportar RDNA4 ainda).
3. Em **Developer / Local Server**, carregue o modelo e inicie o servidor
   (porta padrão `1234`).
4. Valide a qualidade em poucos carros, sem gravar nada no banco:

```bash
python validate_llm.py          # 3 carros; VALIDATE_N=5 para mais
```

> Funciona com qualquer servidor compatível com OpenAI. Para usar Ollama:
> `LLM_BASE_URL=http://localhost:11434/v1` no `.env`.

| Variável | Padrão | Descrição |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:1234/v1` | Endpoint do servidor LLM (LM Studio) |
| `LLM_MODEL` | `qwen2.5-7b-instruct` | Nome do modelo carregado |

## API

- `GET /api/cars?brand=<texto>&days=<n>&limit=<n>` — lista de carros, **ordenada
  por score** de custo-benefício (carros sem nota por último).
  - `brand` — filtro por texto no título (case-insensitive). Vazio = todos.
  - `days` — só anúncios publicados nos últimos N dias (`postDate`). Padrão `14`;
    `0`/vazio = sem limite de data.
  - `limit` — máximo de registros. Padrão `5000`, teto `10000`.
  - Cada carro vem anotado com `score`, `ref_price` (mediana dos pares),
    `price_gap_pct`, `peers`, `ref_basis` e `flags`.
- `GET /api/health` — status do serviço e contagem de carros.
