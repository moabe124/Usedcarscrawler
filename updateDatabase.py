import logging
import time
from datetime import datetime

from pymongo import MongoClient, UpdateOne

import utils.crawlerCore as crawlerCore
from utils.constants import collectionName, databaseName, connectionString, pageLimit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# How long to wait between pages and between full crawl cycles.
PAGE_DELAY_SECONDS = 60
CYCLE_DELAY_SECONDS = 180


def get_collection():
    client = MongoClient(connectionString)
    return client[databaseName][collectionName]


def upsert_cars(collection, cars, page):
    """Upsert ads by announceName. Returns how many existing ads were updated."""
    if not cars:
        logging.info("No cars to upsert on page %s", page)
        return 0

    operations = [
        UpdateOne({"announceName": car["announceName"]}, {"$set": car}, upsert=True)
        for car in cars
    ]
    result = collection.bulk_write(operations)

    if result.modified_count > 0:
        logging.info("%d duplicates (updates) on page %s",
                     result.modified_count, page)
    else:
        logging.info("Zero duplicates on page %s", page)

    return result.modified_count


def crawl_cycle(collection):
    """Walk pages until we hit known ads (duplicates) or the page limit."""
    driver = crawlerCore.configure_driver()
    try:
        page = 1
        duplicates = 0
        while duplicates == 0 and page < pageLimit:
            cars = crawlerCore.getCars(driver, "", page)
            duplicates = upsert_cars(collection, cars, page)
            logging.info("######### page %s done #########", page)
            page += 1
            if duplicates == 0 and page < pageLimit:
                time.sleep(PAGE_DELAY_SECONDS)
    finally:
        driver.quit()


if __name__ == "__main__":
    collection = get_collection()
    while True:
        logging.info("Crawl cycle started")
        try:
            crawl_cycle(collection)
        except Exception as exc:
            logging.exception("Crawl cycle failed: %s", exc)
        logging.info("Cycle finished, sleeping at %s", datetime.now())
        time.sleep(CYCLE_DELAY_SECONDS)
