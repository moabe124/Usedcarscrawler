# -*- coding: utf-8 -*-
"""Tiny client for a local, OpenAI-compatible LLM server (LM Studio / Ollama).

Defaults to LM Studio's endpoint. Switch backends with env vars only:
  LLM_BASE_URL  (default http://localhost:1234/v1)   # Ollama: http://localhost:11434/v1
  LLM_MODEL     (default qwen2.5-7b-instruct)
No extra pip dependency: uses urllib from the standard library.
"""
import os
import json
import logging
import urllib.request
import urllib.error

try:
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:1234/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5-7b-instruct")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "not-needed")

SYSTEM_PROMPT = (
    "Você é um avaliador de anúncios de carros usados no Brasil. "
    "Analise SOMENTE as informações fornecidas (dados do anúncio + descrição "
    "escrita pelo vendedor). Nunca invente dados. Quando uma informação não "
    "estiver clara, use null. Responda SEMPRE em português e SOMENTE com um "
    "objeto JSON válido, sem texto fora do JSON, neste formato:\n"
    "{\n"
    '  "resumo": "2-3 frases com sua opinião sobre o carro e o anúncio",\n'
    '  "score": 0-10 (custo-benefício; considere preço vs ano, km e o que a '
    'descrição revela sobre o estado),\n'
    '  "score_motivo": "1 frase justificando o score",\n'
    '  "tags": ["lista", "curta", "de", "tags"],\n'
    '  "campos": {\n'
    '    "unico_dono": true|false|null,\n'
    '    "sinistro": true|false|null,\n'
    '    "ipva_pago": true|false|null,\n'
    '    "aceita_troca": true|false|null,\n'
    '    "financiavel": true|false|null,\n'
    '    "revisoes_em_dia": true|false|null\n'
    "  },\n"
    '  "red_flags": ["sinais de alerta ou inconsistências; [] se nenhum"]\n'
    "}"
)


def _nb():  # nullable boolean
    return {"type": ["boolean", "null"]}


# JSON Schema for structured output. LM Studio turns this into a grammar and
# forces the model to fill every field — which also prevents empty replies.
EVAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["resumo", "score", "score_motivo", "tags", "campos", "red_flags"],
    "properties": {
        "resumo": {"type": "string"},
        "score": {"type": "number"},
        "score_motivo": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "campos": {
            "type": "object",
            "additionalProperties": False,
            "required": ["unico_dono", "sinistro", "ipva_pago",
                         "aceita_troca", "financiavel", "revisoes_em_dia"],
            "properties": {
                "unico_dono": _nb(), "sinistro": _nb(), "ipva_pago": _nb(),
                "aceita_troca": _nb(), "financiavel": _nb(), "revisoes_em_dia": _nb(),
            },
        },
        "red_flags": {"type": "array", "items": {"type": "string"}},
    },
}


def _post(payload, timeout):
    req = urllib.request.Request(
        LLM_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_API_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail[:600]}") from None


def chat(messages, temperature=0.2, max_tokens=800, schema=None, timeout=180):
    """Call the chat/completions endpoint; return the assistant message text.

    Pass `schema` (a JSON Schema dict) to enforce structured JSON output.
    """
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "car_eval", "strict": True, "schema": schema},
        }

    try:
        body = _post(payload, timeout)
    except RuntimeError as exc:
        # Older/leaner runtimes may reject response_format; retry as plain text.
        if schema and "HTTP 400" in str(exc):
            logging.warning("Retrying without response_format (%s)", exc)
            payload.pop("response_format", None)
            body = _post(payload, timeout)
        else:
            raise

    choice = body["choices"][0]
    msg = choice.get("message", {})
    content = (msg.get("content") or "").strip()
    if not content:
        # Reasoning models stash the answer in reasoning_content and leave
        # content empty; surface what the server actually returned so we can
        # tell "model is reasoning" from "generation got cut off".
        content = (msg.get("reasoning_content") or "").strip()
        logging.warning(
            "Empty content (finish_reason=%s, usage=%s, msg_keys=%s)",
            choice.get("finish_reason"), body.get("usage"), list(msg.keys()))
    return content


def _extract_json(text):
    """Pull the first balanced {...} object out of free-form model text.

    Reasoning models answer with their chain-of-thought followed by the JSON, so
    we can't json.loads() the whole reply — we scan for the first complete object
    (respecting strings/escapes) and try each "{" until one parses.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break  # malformed candidate; advance to the next "{"
        start = text.find("{", start + 1)
    return None


def evaluate_car(car, description):
    """Run the heuristic evaluation for one car. Returns a parsed dict.

    Primary path lets the (reasoning) model think freely and emit JSON at the
    end — better judgement on score/red_flags than a grammar-constrained reply.
    Falls back to strict json_schema if no JSON can be recovered, and finally to
    `_raw` so the caller can still inspect what the model produced.
    """
    user = (
        f"DADOS DO ANÚNCIO:\n"
        f"- Título: {car.get('announceName', '')}\n"
        f"- Preço: R$ {car.get('price', '')}\n"
        f"- Ano: {car.get('year', '')}\n"
        f"- KM: {car.get('kilometer', '')}\n"
        f"- Cor: {car.get('color', '')}\n"
        f"- Local: {car.get('location', '')}\n\n"
        f"DESCRIÇÃO DO VENDEDOR:\n{description.strip() or '(sem descrição)'}"
    )
    # Gemma's chat template rejects a separate "system" role, so we merge the
    # instructions into a single user message — works across local models.
    messages = [{"role": "user", "content": SYSTEM_PROMPT + "\n\n" + user}]

    # Free-form: no grammar, so the model can reason before the JSON. Reasoning
    # eats tokens, hence the bigger budget.
    content = chat(messages, max_tokens=2000)
    data = _extract_json(content)
    if data is not None:
        return data

    # Fallback: force structured output via grammar. No room to "think", but
    # guarantees a parseable object on non-reasoning models / odd replies.
    logging.warning("No JSON in free-form reply; retrying with strict schema")
    content = chat(messages, schema=EVAL_SCHEMA)
    data = _extract_json(content)
    if data is not None:
        return data
    logging.warning("LLM returned non-JSON; keeping raw text")
    return {"_raw": content}


def ping():
    """Quick check that the server is up and a model is loaded."""
    try:
        with urllib.request.urlopen(
                LLM_BASE_URL.rstrip("/") + "/models", timeout=10) as resp:
            models = json.loads(resp.read())
        ids = [m.get("id") for m in models.get("data", [])]
        return True, ids
    except urllib.error.URLError as exc:
        return False, str(exc)
