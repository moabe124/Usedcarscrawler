# -*- coding: utf-8 -*-
"""Collect a few car descriptions from OLX (NON-headless) into samples.json.

A visible Chrome window clears Cloudflare more reliably. This only scrapes
descriptions — it does NOT call the LLM and does NOT touch the database.

Usage:
  python collect_samples.py            # 4 samples
  SAMPLE_N=6 python collect_samples.py
"""
import os
import json
import time
import logging

os.environ.setdefault("HEADLESS", "0")  # visible window

from updateDatabase import get_collection
from utils.crawlerCore import configure_driver, fetch_detail, warm_up

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

N = int(os.environ.get("SAMPLE_N", "4"))
DELAY = int(os.environ.get("DETAIL_DELAY_SECONDS", "20"))
OUT = "samples.json"


def main():
    col = get_collection()
    # Over-fetch candidates so a few blocks don't starve us of N samples.
    cars = list(col.find().sort("price", 1).limit(N * 3))
    samples = []

    driver = configure_driver(headless=False)
    try:
        if not warm_up(driver):
            print("⚠️  Cloudflare não liberou nem a listagem; tente de novo em alguns minutos.")
            return
        for car in cars:
            if len(samples) >= N:
                break
            desc = fetch_detail(driver, car["link"])
            status = f"{len(desc)} chars" if desc else "BLOQUEADO/vazio"
            print(f"- {car['announceName'][:42]:44} -> {status}")
            if desc:
                samples.append({
                    "announceName": car["announceName"], "price": car["price"],
                    "year": car.get("year"), "kilometer": car.get("kilometer"),
                    "color": car.get("color", ""), "location": car.get("location", ""),
                    "link": car["link"], "description": desc,
                })
            time.sleep(DELAY)  # be gentle between detail pages
    finally:
        driver.quit()

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)
    print(f"\n✅ {len(samples)} sample(s) salvos em {OUT}")


if __name__ == "__main__":
    main()
