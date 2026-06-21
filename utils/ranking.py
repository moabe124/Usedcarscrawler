# -*- coding: utf-8 -*-
"""Cost-benefit ranking for used-car ads, using only listing data.

No external price table and no LLM: each car is scored against its *peers*
already in the database (same model + similar year). The captured market is the
yardstick. Everything here is pure functions over plain dicts, so it can be
unit-tested without a database (see the __main__ self-test at the bottom).
"""
import re
import unicodedata
from collections import defaultdict
from statistics import median

# Tunables (override via app/env if desired).
KM_PER_YEAR = 12000        # expected yearly mileage used to judge wear
MIN_PEERS = 3              # smallest peer group we trust for a reference price
YEAR_WINDOW = 3           # widen the peer year window up to ±this many years
PRICE_W = 0.65            # weight of the price gap in the final score
KM_W = 0.35               # weight of the mileage factor in the final score

# A price this far below peers is "too good to be true" (hidden problem / scam).
SUSPECT_GAP = 0.40
# Mileage below this fraction of the expected km looks like a tampered odometer.
SUSPECT_KM_RATIO = 0.20
# Suspicious cars stay visible but are capped so they never top the ranking.
SUSPECT_SCORE_CAP = 60

# Collapse common brand aliases so "VW Gol" and "Volkswagen Gol" group together.
BRAND_ALIASES = {
    "vw": "volkswagen", "gm": "chevrolet", "mercedes": "mercedesbenz",
    "mb": "mercedesbenz", "land": "landrover",
}


def strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


def parse_int(value):
    """Pull a plain integer out of strings like 'R$ 32.000' or '72.000 km'."""
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) if digits else None


def parse_year(value):
    match = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return int(match.group()) if match else None


def model_key(title):
    """A coarse 'brand model' key from the ad title, e.g.
    'Volkswagen Gol 1.0 Flex 2015' -> 'volkswagen gol'.
    Takes the first two alphabetic words (numbers/versions/year are skipped)."""
    words = re.findall(r"[A-Za-zÀ-ÿ]{2,}", str(title or ""))
    words = [strip_accents(w).lower() for w in words]
    if words:
        words[0] = BRAND_ALIASES.get(words[0], words[0])
    return " ".join(words[:2])


def build_reference(cars):
    """Index peer prices by (model, year), model, and year for fallback lookups.

    `cars` is any iterable of dicts with announceName/year/price. This is meant to
    be built from the whole collection so references stay stable even when the
    caller is showing a narrow (e.g. last-14-days) slice."""
    by_model_year = defaultdict(list)
    for car in cars:
        price = car.get("price")
        if not price:
            continue
        mk = model_key(car.get("announceName"))
        yr = parse_year(car.get("year"))
        if mk and yr:
            by_model_year[(mk, yr)].append(price)
    return {"model_year": by_model_year}


def reference_price(ref, mk, yr):
    """Peer median for a car: same model, widening the year window (0..YEAR_WINDOW)
    until we have at least MIN_PEERS samples. Returns (median, count, basis).

    We deliberately do NOT fall back to "same model, any year" or "same year, any
    model": price is dominated by age, so those cohorts make an old car look like a
    bargain just for being old. If there aren't enough similar-age peers, we return
    no reference (the car stays unscored) rather than a misleading one."""
    if not (mk and yr):
        return None, 0, "insuficiente"
    for w in range(0, YEAR_WINDOW + 1):
        prices = []
        for dy in range(-w, w + 1):
            prices += ref["model_year"].get((mk, yr + dy), [])
        if len(prices) >= MIN_PEERS:
            basis = "modelo+ano" if w == 0 else f"modelo+ano±{w}"
            return median(prices), len(prices), basis
    return None, 0, "insuficiente"


def _clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def score_car(car, ref, now_year):
    """Compute a 0-100 cost-benefit score and flags for one car.

    Returns a dict to merge into the car. score is None when there aren't enough
    peers to form a fair reference (we don't guess)."""
    price = car.get("price")
    mk = model_key(car.get("announceName"))
    yr = parse_year(car.get("year"))
    km = parse_int(car.get("kilometer"))

    ref_price, peers, basis = reference_price(ref, mk, yr)
    flags = []

    # Price gap: positive means cheaper than comparable cars.
    price_gap = None
    if ref_price and price:
        price_gap = (ref_price - price) / ref_price
        if price_gap >= SUSPECT_GAP:
            flags.append("preço suspeito")
        elif price_gap >= 0.10:
            flags.append("barato")
        elif price_gap <= -0.10:
            flags.append("caro")

    # Mileage factor: less driven than expected for its age is good.
    age = max(now_year - yr, 0) if yr else None
    km_expected = age * KM_PER_YEAR if age is not None else None
    km_factor = 0.5  # neutral when we can't judge
    if km is not None and km_expected:
        km_ratio = km / km_expected
        # ratio 1.0 -> neutral(0.5); 0.5 -> ~0.85; 1.5 -> ~0.15
        km_factor = _clamp01(1.0 - (km_ratio - 1.0))
        if km_ratio <= SUSPECT_KM_RATIO:
            flags.append("km suspeito")
        elif km_ratio < 0.6:
            flags.append("km baixo")
        elif km_ratio > 1.4:
            flags.append("km alto")

    score = None
    if price_gap is not None:
        # Map a ±25% price gap onto 0..1, blend with the mileage factor.
        price_factor = _clamp01((price_gap + 0.25) / 0.50)
        score = round(100 * (PRICE_W * price_factor + KM_W * km_factor))
        # A too-good-to-be-true ad shouldn't outrank honest deals: cap it but
        # keep the flag so the buyer sees both the price and the warning.
        if "preço suspeito" in flags or "km suspeito" in flags:
            score = min(score, SUSPECT_SCORE_CAP)

    return {
        "score": score,
        "ref_price": round(ref_price) if ref_price else None,
        "price_gap_pct": round(price_gap * 100, 1) if price_gap is not None else None,
        "km_expected": km_expected,
        "peers": peers,
        "ref_basis": basis,
        "flags": flags,
    }


def rank_cars(cars, reference_population=None, now_year=None):
    """Annotate each car in `cars` with a cost-benefit score in place.

    reference_population: iterable used to build peer prices (defaults to `cars`).
    Returns the same list, sorted by score descending (unscored cars last)."""
    import datetime
    now_year = now_year or datetime.date.today().year
    ref = build_reference(reference_population if reference_population is not None else cars)
    for car in cars:
        car.update(score_car(car, ref, now_year))
    cars.sort(key=lambda c: (c.get("score") is not None, c.get("score") or 0),
              reverse=True)
    return cars


if __name__ == "__main__":
    # Self-test with synthetic data — no DB required.
    sample = [
        {"announceName": "Volkswagen Gol 1.0 Flex 2015", "price": 32000,
         "year": "2015", "kilometer": "98.000 km"},
        {"announceName": "VW Gol 1.6 2015", "price": 28000,
         "year": "2015", "kilometer": "60.000 km"},   # cheaper + low km -> top
        {"announceName": "Volkswagen Gol Trend 2015", "price": 36000,
         "year": "2015", "kilometer": "120.000 km"},  # pricier + high km
        {"announceName": "Volkswagen Gol 2015", "price": 15000,
         "year": "2015", "kilometer": "5.000 km"},    # too cheap + impossible km
        {"announceName": "Fiat Uno Way 2017", "price": 38500,
         "year": "2017", "kilometer": "72.000 km"},   # lone model -> few peers
    ]
    for c in rank_cars(sample, now_year=2026):
        print(f"score={c['score']!s:>4}  gap={c['price_gap_pct']!s:>6}%  "
              f"peers={c['peers']} ({c['ref_basis']})  flags={c['flags']}  "
              f"| {c['announceName']}")
