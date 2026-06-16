# -*- coding: utf-8 -*-
"""Validation harness: fetch the description of a FEW cars and run the local LLM.

It does NOT write anything to the database — the goal is to eyeball quality
before wiring this into the crawler. Be polite: small N, generous delays.

Usage:
  python validate_llm.py            # 3 cars
  VALIDATE_N=5 python validate_llm.py
"""
import os
import json
import time
import logging

from updateDatabase import get_collection
from utils.crawlerCore import configure_driver, fetch_detail, warm_up
import llm

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

N = int(os.environ.get("VALIDATE_N", "3"))
DETAIL_DELAY_SECONDS = int(os.environ.get("DETAIL_DELAY_SECONDS", "30"))


def main():
    ok, info = llm.ping()
    if not ok:
        print(f"\n⚠️  LLM server não respondeu em {llm.LLM_BASE_URL}")
        print(f"   Detalhe: {info}")
        print("   Suba o LM Studio (Local Server) com um modelo carregado e tente de novo.\n")
        return
    print(f"LLM OK em {llm.LLM_BASE_URL} | modelos: {info}\n")

    # Offline mode: validate the LLM on a fixed description, no OLX/Cloudflare.
    if os.environ.get("VALIDATE_SAMPLE") == "1":
        car = {"announceName": "Chevrolet Calibra 16V 1995", "price": 5500,
               "year": "1995", "kilometer": "55.533 km", "color": "",
               "location": "Recife - PE"}
        desc = ("Vendo Chevrolet Calibra, pouco rodado (55.533 km).\n"
                "Para mais informações, entre em contato!")
        print("MODO SAMPLE (sem OLX) —", car["announceName"])
        print(f"\n  DESCRIÇÃO:\n  {desc}\n")
        print("  AVALIAÇÃO DO LLM:")
        print(json.dumps(llm.evaluate_car(car, desc), ensure_ascii=False, indent=2))
        return

    col = get_collection()
    cars = list(col.find().sort("price", 1).limit(N))
    print(f"Validando {len(cars)} carro(s)...\n")

    driver = configure_driver()
    try:
        if not warm_up(driver):
            print("⚠️  Não consegui passar pelo Cloudflare na listagem; "
                  "as descrições provavelmente virão vazias.\n")
        for i, car in enumerate(cars, 1):
            print("=" * 70)
            print(f"[{i}/{len(cars)}] {car['announceName'][:55]}")
            print(f"    R$ {car['price']} | {car.get('year')} | {car.get('kilometer')}")
            print(f"    {car['link']}")

            description = fetch_detail(driver, car["link"])
            if not description:
                print("    (sem descrição / bloqueado — pulando)\n")
                continue
            print(f"\n  DESCRIÇÃO:\n  {description[:400]}\n")

            result = llm.evaluate_car(car, description)
            print("  AVALIAÇÃO DO LLM:")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print()

            if i < len(cars):
                time.sleep(DETAIL_DELAY_SECONDS)  # be gentle with OLX
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
