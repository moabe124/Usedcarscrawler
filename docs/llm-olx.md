# Como funciona: LLM + OLX

Documento curto explicando o caminho da informação, do anúncio na OLX até a
avaliação feita pelo LLM local.

## Visão geral do fluxo

```
OLX (listagem)  →  OLX (detalhe)  →  descrição  →  LLM local  →  JSON de avaliação
   crawler          fetch_detail     JSON-LD       LM Studio       resumo/score/tags
```

1. **Listagem** — o crawler abre a página de busca da OLX e lê cada card
   (título, preço, ano, km). Isso já popula o banco.
2. **Detalhe** — para um carro específico, abrimos a página do anúncio dele.
3. **Descrição** — extraímos o texto que o vendedor escreveu.
4. **LLM** — mandamos os dados + a descrição para um modelo rodando na sua
   máquina, que devolve uma avaliação estruturada (JSON).

## A parte da OLX

A OLX fica atrás do **Cloudflare** (proteção anti-bot). Por isso:

- Usamos Selenium com **Chrome em modo `headless=new`** + alguns ajustes de
  "stealth" (user-agent real, flags anti-automação). Isso passa pelo desafio do
  Cloudflare; o headless "puro" é bloqueado.
- A descrição **não** está num HTML fácil de raspar — ela vem dentro de um bloco
  **JSON-LD** (`<script type="application/ld+json">`), no campo
  `makesOffer.itemOffered.description`. Lemos esse JSON em vez de caçar `<div>`s,
  o que é bem mais estável quando a OLX muda o layout.
- Visitar a tela de detalhe gera **mais requisições** (uma por carro), então isso
  é feito com calma (pausas entre anúncios) e em poucos carros por vez, para não
  chamar atenção do anti-bot.

Código relevante: `fetch_detail()` e `extract_description()` em
[`utils/crawlerCore.py`](../utils/crawlerCore.py).

## A parte do LLM

O modelo roda **localmente** (nada sai da sua máquina). Falamos com ele por uma
API **compatível com OpenAI**:

- Padrão: **LM Studio** em `http://localhost:1234/v1` com o modelo
  `qwen2.5-7b-instruct`.
- Trocar para Ollama é só mudar `LLM_BASE_URL` no `.env`.
- O cliente ([`llm.py`](../llm.py)) não usa nenhuma biblioteca extra — fala HTTP
  direto com `urllib`.

Mandamos um **prompt em português** com os dados do anúncio + a descrição, e
pedimos a resposta **só em JSON**:

```json
{
  "resumo": "opinião curta sobre o carro e o anúncio",
  "score": 7,
  "score_motivo": "preço justo para o ano e km, único dono",
  "tags": ["único dono", "ipva pago"],
  "campos": {
    "unico_dono": true,
    "sinistro": null,
    "ipva_pago": true,
    "aceita_troca": null,
    "financiavel": null,
    "revisoes_em_dia": true
  },
  "red_flags": []
}
```

Regras dadas ao modelo: usar **só** o que foi fornecido, nunca inventar, e usar
`null` quando a informação não estiver clara.

## Como rodar a validação

A ideia agora é **validar a qualidade** antes de gravar qualquer coisa no banco:

```bash
python validate_llm.py        # pega 3 carros, mostra descrição + avaliação
```

O script:
1. Confere se o servidor LLM está no ar (avisa se não estiver).
2. Pega poucos carros do banco.
3. Para cada um: busca a descrição na OLX e roda o LLM.
4. **Imprime** o resultado — não grava nada.

## Próximos passos (ainda não feitos)

- Ajustar o prompt depois de olhar a saída real.
- Decidir quais campos guardar no MongoDB (ex.: `score`, `tags`, `resumo`).
- Eventualmente exibir score/tags no gráfico e na tabela do front.
