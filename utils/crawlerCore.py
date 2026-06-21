import os
import re
import json
import time
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By

from utils.constants import formattedURL, monthsDictionary, PRICE_CEILING

# Brazil time (UTC-3). Etc/GMT-3 is intentionally "inverted": it means UTC-3.
BR_TZ = ZoneInfo("Etc/GMT-3")

YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def translate_date(inputDate):
    """Parse OLX post dates like 'Hoje, 13:45', 'Ontem, 09:10' or '12 mar, 18:30'."""
    splitted = inputDate.split(", ")
    if len(splitted) != 2:
        # Unexpected format: fall back to "now" instead of crashing the whole page.
        logging.warning("Unrecognized date format: %r", inputDate)
        return datetime.now(BR_TZ)

    date_part, hour = splitted[0], splitted[1]
    upper = date_part.upper()

    if "HOJE" in upper:
        day = datetime.now()
    elif "ONTEM" in upper:
        day = datetime.now() - timedelta(days=1)
    else:
        # e.g. "12 mar", "12 de mar.", "12 de março" — OLX varies the wording,
        # so scan the tokens for the day number and a known month (abbrev or full;
        # the first 3 letters of every pt-BR month name match the dictionary keys).
        tokens = date_part.replace(".", " ").split()
        day_num = next((t for t in tokens if t.isdigit()), None)
        month = next((monthsDictionary[t.lower()[:3]]
                      for t in tokens if t.lower()[:3] in monthsDictionary), None)
        if not day_num or not month:
            logging.warning("Unknown month in date: %r", inputDate)
            return datetime.now(BR_TZ)
        parsed = datetime.strptime(
            f"{day_num} {month} {datetime.now().year} {hour}", "%d %B %Y %H:%M")
        parsed = parsed.replace(tzinfo=BR_TZ)
        # Year rollover: OLX dates carry no year, so a date that lands in the
        # future (e.g. "12 dez" parsed in January) really belongs to last year.
        if parsed > datetime.now(BR_TZ) + timedelta(days=1):
            parsed = parsed.replace(year=parsed.year - 1)
        return parsed

    parsed = datetime.strptime(
        f"{day.strftime('%Y %b %d')} {hour}", "%Y %b %d %H:%M")
    return parsed.replace(tzinfo=BR_TZ)


def configure_driver(headless=None):
    """Build the stealth Chrome driver. Set headless=False (or HEADLESS=0 in the
    env) for a visible window, which clears Cloudflare more reliably."""
    if headless is None:
        headless = os.environ.get("HEADLESS", "1") != "0"

    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--log-level=3")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1280,900")
    # Lower the automation fingerprint so OLX's Cloudflare challenge lets us in.
    # Plain headless gets blocked; these tweaks + a real UA pass the managed check.
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(options=chrome_options)


def checkPrice(priceEl):
    if not priceEl:
        return None
    digits = priceEl.get_text().replace("R$", "").replace(".", "").strip()
    return int(digits) if digits.isdigit() else None


def parse_card(card):
    """Parse a single `section.olx-adcard`. Returns a dict or None if unusable.

    The listing card no longer exposes fields by fixed position: detail chips are
    [km, color, engine, bodyType] (variable), and the year lives in the title.
    """
    price = checkPrice(card.select_one("h3.olx-adcard__price"))
    if price is None or price >= PRICE_CEILING:
        return None

    title_el = card.select_one("h2.olx-adcard__title") or card.find("h2")
    title = title_el.get_text(strip=True) if title_el else ""

    link_el = card.select_one("a.olx-adcard__link") or card.find("a", href=True)
    img_el = card.find("img")

    details = [d.get_text(" ", strip=True) for d in card.select(".olx-adcard__detail")]
    kilometer = next((d for d in details if "km" in d.lower()),
                     details[0] if details else "")

    year_matches = YEAR_RE.findall(title)
    year = year_matches[-1] if year_matches else ""

    location_el = card.select_one("p.olx-adcard__location")
    date_el = card.select_one("p.olx-adcard__date")
    post_date = date_el.get_text(strip=True) if date_el else ""

    return {
        "announceName": title,
        "formattedPrice": f"R$ {price:,}".replace(",", "."),
        "price": price,
        "kilometer": kilometer,
        "year": year,
        "color": details[1] if len(details) > 1 else "",
        "engine": details[2] if len(details) > 2 else "",
        "bodyType": details[3] if len(details) > 3 else "",
        "link": link_el["href"] if link_el and link_el.has_attr("href") else "",
        "img": img_el.get("src", "") if img_el else "",
        "location": location_el.get_text(strip=True) if location_el else "",
        "postDate": translate_date(post_date) if post_date else datetime.now(BR_TZ),
        "created": datetime.now(BR_TZ),
    }


def getCars(driver, carBrand="", page=1):
    driver.get(formattedURL(carBrand, page))

    try:
        # Waiting for the cards also gives the Cloudflare challenge time to resolve.
        WebDriverWait(driver, 25).until(
            lambda s: s.find_elements(By.CSS_SELECTOR, "section.olx-adcard"))
    except TimeoutException:
        logging.warning("Timeout: no ad cards on page %s (title=%r)",
                        page, driver.title)
        return []

    entirePage = BeautifulSoup(driver.page_source, "html.parser")

    cars = []
    cards = entirePage.select("section.olx-adcard")
    for card in cards:
        try:
            parsed = parse_card(card)
            if parsed:
                cars.append(parsed)
        except Exception as exc:  # one broken card must not kill the whole page
            logging.warning("Failed to parse a card: %s", exc)

    logging.info("Crawler OK: %d/%d cards parsed on page %s",
                 len(cars), len(cards), page)
    return cars


def _collect_descriptions(obj):
    """Recursively gather every JSON-LD 'description' string."""
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "description" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_collect_descriptions(value))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(_collect_descriptions(value))
    return found


def extract_description(html):
    """Pull the ad description from the detail page's JSON-LD block.

    OLX embeds it in <script type="application/ld+json"> as
    makesOffer.itemOffered.description — far more stable than scraping spans.
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for sc in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if not sc.string:
            continue
        try:
            data = json.loads(sc.string)
        except (ValueError, TypeError):
            continue
        candidates.extend(_collect_descriptions(data))
    if not candidates:
        return ""
    desc = max(candidates, key=len)
    desc = re.sub(r"<[^>]+>", "\n", desc)          # OLX stores literal <br> tags
    return re.sub(r"\n{3,}", "\n\n", desc).strip()


def warm_up(driver):
    """Load the listing once so Cloudflare grants a clearance cookie before we
    hit deep detail links cold (mirrors a real user going from search to an ad)."""
    driver.get(formattedURL("", 1))
    try:
        WebDriverWait(driver, 25).until(
            lambda s: s.find_elements(By.CSS_SELECTOR, "section.olx-adcard"))
        logging.info("Warm-up OK — Cloudflare cleared")
        return True
    except TimeoutException:
        logging.warning("Warm-up failed (title=%r)", driver.title)
        return False


def fetch_detail(driver, link, settle_seconds=5, retries=1, backoff_seconds=15):
    """Visit one ad detail page and return its description text (or '').

    Be gentle: Cloudflare escalates (and blocks the whole session) if you hammer
    deep links. So we do at most one slow retry and otherwise give up on the car.
    Space calls out at the caller level (see validate_llm).
    """
    for attempt in range(retries + 1):
        driver.get(link)
        try:
            WebDriverWait(driver, 20).until(
                lambda s: s.find_elements(
                    By.CSS_SELECTOR, 'script[type="application/ld+json"]'))
            time.sleep(settle_seconds)  # let JS settle / Cloudflare clear
            return extract_description(driver.page_source)
        except TimeoutException:
            logging.warning("Detail blocked/timeout (try %d/%d) for %s (title=%r)",
                            attempt + 1, retries + 1, link, driver.title)
            if attempt < retries:
                time.sleep(backoff_seconds)  # back off slowly; do NOT re-warm-spam
    return ""


if __name__ == "__main__":
    driver = configure_driver()
    try:
        for car in getCars(driver):
            print(car["price"], car["year"], car["announceName"][:40])
    finally:
        driver.quit()
