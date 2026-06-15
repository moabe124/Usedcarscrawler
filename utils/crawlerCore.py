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
        # e.g. "12 mar"
        pieces = date_part.split(" ")
        day_num = pieces[0]
        month_abbr = pieces[1].lower() if len(pieces) > 1 else ""
        month = monthsDictionary.get(month_abbr)
        if not month:
            logging.warning("Unknown month in date: %r", inputDate)
            return datetime.now(BR_TZ)
        parsed = datetime.strptime(
            f"{day_num} {month} {datetime.now().year} {hour}", "%d %B %Y %H:%M")
        return parsed.replace(tzinfo=BR_TZ)

    parsed = datetime.strptime(
        f"{day.strftime('%Y %b %d')} {hour}", "%Y %b %d %H:%M")
    return parsed.replace(tzinfo=BR_TZ)


def configure_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--log-level=3")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    # Selenium 4.6+ ships Selenium Manager: it resolves and downloads the
    # matching chromedriver automatically, no manual binary needed.
    return webdriver.Chrome(options=chrome_options)


def checkPrice(priceText):
    if not priceText:
        return None
    digits = priceText.text.replace("R$", "").replace(".", "").strip()
    return int(digits) if digits.isdigit() else None


def parse_card(carCard):
    """Parse a single ad card. Returns a dict or None if the card is malformed."""
    second_div = carCard.select_one('div:nth-of-type(2)')
    if not second_div:
        return None
    labelGroup = second_div.select('span')
    if len(labelGroup) < 4:
        return None

    price = checkPrice(carCard.select_one("span[color='--color-neutral-130']"))
    if price is None or price >= PRICE_CEILING:
        return None

    title = carCard.select_one("h2")
    img = carCard.select_one("img")

    return {
        "announceName": title.text if title else "",
        "formattedPrice": f"R$ {price:,}".replace(",", "."),
        "price": price,
        "kilometer": labelGroup[0].text,
        "year": labelGroup[1].text,
        "gasType": labelGroup[2].text,
        "shiftType": labelGroup[3].text,
        "link": carCard.attrs.get("href", ""),
        "img": img.attrs.get("src", "") if img else "",
        "location": labelGroup[-3].text if len(labelGroup) >= 3 else "",
        "postDate": translate_date(labelGroup[-1].text),
        "created": datetime.now(BR_TZ),
    }


def getCars(driver, carBrand="", page=1):
    driver.get(formattedURL(carBrand, page))

    try:
        WebDriverWait(driver, 10).until(
            lambda s: s.find_element(By.CSS_SELECTOR, "#ad-list").is_displayed())
    except TimeoutException:
        logging.warning("Timeout: #ad-list not found on page %s", page)
        return []

    entirePage = BeautifulSoup(driver.page_source, "html.parser")

    cars = []
    cards = entirePage.select('li a[data-ds-component="DS-AdCardHorizontal"]')
    for carCard in cards:
        try:
            car = parse_card(carCard)
            if car:
                cars.append(car)
        except Exception as exc:  # one broken card must not kill the whole page
            logging.warning("Failed to parse a card: %s", exc)

    logging.info("Crawler OK: %d/%d cards parsed on page %s",
                 len(cars), len(cards), page)
    return cars


if __name__ == "__main__":
    driver = configure_driver()
    try:
        print(getCars(driver))
    finally:
        driver.quit()
