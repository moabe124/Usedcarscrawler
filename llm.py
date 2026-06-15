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


def chat(messages, temperature=0.2, max_tokens=700, json_mode=True, timeout=180):
    """Call the chat/completions endpoint; return the assistant message text."""
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    req = urllib.request.Request(
        LLM_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_API_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read())
    return body["choices"][0]["message"]["content"]


def evaluate_car(car, description):
    """Run the heuristic evaluation for one car. Returns a parsed dict.

    Adds `_raw` with the model's raw text if the JSON can't be parsed, so the
    caller can inspect what went wrong during validation.
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
    content = chat([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ])
    try:
        return json.loads(content)
    except json.JSONDecodeError:
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
