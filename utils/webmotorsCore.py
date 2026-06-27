# -*- coding: utf-8 -*-
"""Webmotors collector — sibling of crawlerCore.py (OLX).

Webmotors sits behind PerimeterX, which blocks plain stealth Selenium outright
(the OLX/Cloudflare trick is not enough). undetected-chromedriver clears it, so
this module drives Chrome through `uc` and reads the rendered listing DOM. We
deliberately do NOT call the site's internal /api/search/car endpoint: it's PX-
gated and the in-browser fetch hangs under this Python; the DOM is enough.

Output dicts use the SAME shape as crawlerCore.parse_card so the rest of the
pipeline (upsert, ranking, web UI) treats both sources uniformly. The ad id is
namespaced ("wm-<id>") so it can never collide with OLX's numeric ids, and a
`source` field tags the origin.
"""

import os
import re
import time
import logging
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from utils.constants import PRICE_CEILING

BR_TZ = ZoneInfo("Etc/GMT-3")

# Pagination: the modern /comprar SPA ignores ?page/?p, but the legacy
# "necessidade.comprar" endpoint renders the same listing AND honors &page=N.
# It's geo-filtered (estadocidade) to match the OLX crawler's scope (estado-pe).
# Note: marca1=estoque returns all stock (used + seminovo + 0km); PRICE_CEILING
# trims the outliers. Override the region via WEBMOTORS_ESTADOCIDADE.
BASE_URL = "https://www.webmotors.com.br/carros/pe/estoque/necessidade.comprar"
ESTADOCIDADE = os.environ.get("WEBMOTORS_ESTADOCIDADE", "Pernambuco")

# Ad detail URL: /comprar/{brand}/{model}/{version}/{doors}/{year}/{id}
# year may be "2023" or "2023-2024" (fabrication-model). The trailing id is the
# stable unique identity we key on.
AD_HREF_RE = re.compile(
    r"/comprar/([^/]+)/([^/]+)/([^/]+)/[^/]+/(\d{4}(?:-\d{4})?)/(\d{6,})$")

PRICE_RE = re.compile(r"R\$[\s ]*([\d.]+)")
KM_RE = re.compile(r"([\d.]+)\s*km", re.IGNORECASE)
YEAR_PAIR_RE = re.compile(r"(\d{4})/(\d{4})")
LOCATION_RE = re.compile(r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ.\s'-]+\([A-Z]{2}\))")


def _chrome_major():
    """Major version of the installed Chrome, so uc fetches a matching driver.
    uc's auto-detect grabbed the wrong (newer) driver here, causing
    SessionNotCreated; pinning version_main avoids that."""
    env = os.environ.get("CHROME_MAJOR")
    if env and env.isdigit():
        return int(env)
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key = winreg.OpenKey(hive, r"Software\Google\Chrome\BLBeacon")
                version, _ = winreg.QueryValueEx(key, "version")
                return int(version.split(".")[0])
            except OSError:
                continue
    except Exception:  # winreg only exists on Windows; fall through elsewhere
        pass
    return None


def configure_driver(headless=None):
    """Build an undetected-chromedriver session that clears PerimeterX.

    Must run HEADED: PerimeterX reliably blocks the headless fingerprint (the
    listing returns "Access to this page has been denied"), while a visible
    window clears it. So this defaults to visible; opt into headless only via
    WEBMOTORS_HEADLESS=1 (e.g. behind a virtual display like xvfb). This is
    independent of the OLX driver's HEADLESS, which works headless on Cloudflare."""
    import undetected_chromedriver as uc

    if headless is None:
        headless = os.environ.get("WEBMOTORS_HEADLESS", "0") != "0"
    opts = uc.ChromeOptions()
    opts.add_argument("--window-size=1320,1000")
    # 'eager': return once the DOM is interactive instead of waiting for the
    # network to idle (PX keeps background JS alive, which stalls 'normal').
    opts.page_load_strategy = "eager"
    return uc.Chrome(options=opts, headless=headless, version_main=_chrome_major())


def quit_driver(driver):
    """uc's __del__/quit raises a harmless WinError 6 on Windows; swallow it."""
    try:
        driver.quit()
    except Exception:
        pass


def listing_url(page=1):
    params = urlencode({
        "tipoveiculo": "carros",
        "estadocidade": ESTADOCIDADE,
        "marca1": "estoque",
        "page": page,
        "necessidade": "Comprar",
    })
    return f"{BASE_URL}?{params}"


def _price_from_text(text):
    """Largest R$ value in the card text (the asking price, never an installment)."""
    prices = []
    for raw in PRICE_RE.findall(text):
        digits = raw.replace(".", "")
        if digits.isdigit():
            prices.append(int(digits))
    return max(prices) if prices else None


def _title_from_url(brand, model, version):
    """Build a readable, OLX-compatible title from the URL slugs. The first two
    alphabetic words must be brand+model so utils.ranking groups Webmotors and
    OLX ads of the same model together."""
    parts = " ".join(s.replace("-", " ") for s in (brand, model, version))
    return parts.title().strip()


def parse_card(container, anchor_href):
    """Parse one rendered ad card. Returns a dict (OLX-shaped) or None."""
    match = AD_HREF_RE.search(anchor_href.split("?")[0])
    if not match:
        return None
    brand, model, version, year_str, ad_id = match.groups()

    text = container.get_text(" ", strip=True)

    price = _price_from_text(text)
    if price is None or price >= PRICE_CEILING:
        return None

    km_match = KM_RE.search(text)
    kilometer = f"{km_match.group(1)} km" if km_match else ""

    # Prefer the fabrication/model pair shown on the card; fall back to the URL.
    year_pair = YEAR_PAIR_RE.search(text)
    year = year_pair.group(2) if year_pair else year_str.split("-")[-1]

    loc_match = LOCATION_RE.search(text)
    location = loc_match.group(1).strip() if loc_match else ""

    img_el = container.find("img")
    img = ""
    if img_el:
        img = img_el.get("src") or img_el.get("data-src") or ""

    return {
        "adId": f"wm-{ad_id}",
        "source": "webmotors",
        "announceName": _title_from_url(brand, model, version),
        "formattedPrice": f"R$ {price:,}".replace(",", "."),
        "price": price,
        "kilometer": kilometer,
        "year": year,
        "color": "",
        "engine": "",
        "bodyType": "",
        "link": anchor_href.split("?")[0],
        "img": img,
        "location": location,
        # Webmotors cards carry no post date; treat "seen now" as recency so
        # active listings stay inside the UI's default date window.
        "postDate": datetime.now(BR_TZ),
        "created": datetime.now(BR_TZ),
    }


def _find_card_container(anchor):
    """Walk up to the smallest ancestor that holds the whole card (has a price
    and a km figure). Avoids brittle hashed CSS-module class names."""
    node = anchor
    for _ in range(8):
        node = node.parent
        if node is None:
            return None
        text = node.get_text(" ", strip=True)
        if "R$" in text and KM_RE.search(text):
            return node
    return None


def parse_listing(html):
    """Parse every ad card in a rendered listing page into OLX-shaped dicts."""
    soup = BeautifulSoup(html, "html.parser")
    cars, seen = [], set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].split("?")[0]
        match = AD_HREF_RE.search(href)
        if not match:
            continue
        ad_id = match.group(5)
        if ad_id in seen:
            continue
        seen.add(ad_id)
        container = _find_card_container(anchor)
        if container is None:
            continue
        try:
            parsed = parse_card(container, href)
            if parsed:
                cars.append(parsed)
        except Exception as exc:  # one bad card must not kill the page
            logging.warning("Failed to parse a Webmotors card: %s", exc)
    return cars


def _is_blocked(driver):
    return "denied" in (driver.title or "").lower()


def getCars(driver, page=1, settle_seconds=10, retries=1, backoff_seconds=15):
    """Load one listing page and return parsed cars.

    Mirrors the proven PoC path: get + sleep + page_source. We avoid
    WebDriverWait/find_elements because that command channel hangs with uc on
    this Python; driver.get and .page_source are reliable.

    A cold first load is sometimes blocked by PerimeterX and then clears once the
    session warms up, so we retry once (gently) before giving up on the page."""
    for attempt in range(retries + 1):
        driver.get(listing_url(page))
        time.sleep(settle_seconds)  # let React paint the cards / PX settle
        if not _is_blocked(driver):
            cars = parse_listing(driver.page_source)
            logging.info("Webmotors OK: %d cars parsed on page %s", len(cars), page)
            return cars
        logging.warning("Webmotors blocked by PerimeterX on page %s (try %d/%d)",
                        page, attempt + 1, retries + 1)
        if attempt < retries:
            time.sleep(backoff_seconds)  # let the session warm; don't hammer
    return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")
    drv = configure_driver()
    try:
        for car in getCars(drv):
            print(car["price"], car["year"], car["kilometer"],
                  "|", car["announceName"][:45], "|", car["adId"])
    finally:
        quit_driver(drv)
