import os
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

# Backfill mode: when the DB has fewer than THRESHOLD records, crawl pages
# continuously (ignoring the duplicate-stop heuristic) until it holds TARGET
# records. MAX_PAGES caps it so we never loop forever.
BACKFILL_THRESHOLD = int(os.environ.get("BACKFILL_THRESHOLD", "200"))
BACKFILL_TARGET = int(os.environ.get("BACKFILL_TARGET", "500"))
BACKFILL_MAX_PAGES = int(os.environ.get("BACKFILL_MAX_PAGES", "30"))


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


def backfill_cycle(collection):
    """DB is nearly empty: keep crawling pages until it holds BACKFILL_TARGET
    records (or we run out of pages / hit the safety cap)."""
    driver = crawlerCore.configure_driver()
    try:
        page = 1
        while page <= BACKFILL_MAX_PAGES:
            count = collection.count_documents({})
            if count >= BACKFILL_TARGET:
                logging.info("Backfill done: %d records (target %d)",
                             count, BACKFILL_TARGET)
                break

            cars = crawlerCore.getCars(driver, "", page)
            if not cars:
                logging.info("Backfill: no cars on page %s; stopping early", page)
                break
            upsert_cars(collection, cars, page)
            logging.info("Backfill: %d records after page %s",
                         collection.count_documents({}), page)

            page += 1
            if collection.count_documents({}) < BACKFILL_TARGET and page <= BACKFILL_MAX_PAGES:
                time.sleep(PAGE_DELAY_SECONDS)
    finally:
        driver.quit()


if __name__ == "__main__":
    collection = get_collection()
    while True:
        count = collection.count_documents({})
        try:
            if count < BACKFILL_THRESHOLD:
                logging.info("Only %d records (< %d): backfilling toward %d",
                             count, BACKFILL_THRESHOLD, BACKFILL_TARGET)
                backfill_cycle(collection)
            else:
                logging.info("Crawl cycle started (%d records)", count)
                crawl_cycle(collection)
        except Exception as exc:
            logging.exception("Cycle failed: %s", exc)
        logging.info("Cycle finished, sleeping at %s", datetime.now())
        time.sleep(CYCLE_DELAY_SECONDS)
