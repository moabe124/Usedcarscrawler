import os
import logging
import time
import random
import threading
from datetime import datetime

from pymongo import MongoClient, UpdateOne

import utils.crawlerCore as olxCore
import utils.webmotorsCore as wmCore
from utils.constants import collectionName, databaseName, connectionString, pageLimit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(threadName)s] %(message)s",
)

# --- OLX cadence (Cloudflare; the existing stealth driver handles it) ---------
PAGE_DELAY_SECONDS = 60
CYCLE_DELAY_SECONDS = 180

# Backfill mode: when a source has fewer than THRESHOLD records, crawl pages
# continuously (ignoring the duplicate-stop heuristic) until it holds TARGET
# records. MAX_PAGES caps it so we never loop forever.
BACKFILL_THRESHOLD = int(os.environ.get("BACKFILL_THRESHOLD", "200"))
BACKFILL_TARGET = int(os.environ.get("BACKFILL_TARGET", "500"))
BACKFILL_MAX_PAGES = int(os.environ.get("BACKFILL_MAX_PAGES", "30"))

# --- Webmotors cadence (PerimeterX; stricter, so be gentler than OLX) ----------
# Deliberately slow: there's no rush to collect, and spacing requests out is the
# best defense against an IP ban. Tune via env if you ever need it faster.
WM_PAGE_LIMIT = int(os.environ.get("WEBMOTORS_PAGE_LIMIT", "5"))
WM_PAGE_DELAY = int(os.environ.get("WEBMOTORS_PAGE_DELAY", "150"))    # ~2.5 min between pages
WM_CYCLE_DELAY = int(os.environ.get("WEBMOTORS_CYCLE_DELAY", "900"))  # ~15 min between cycles
WM_BACKFILL_THRESHOLD = int(os.environ.get("WEBMOTORS_BACKFILL_THRESHOLD", "200"))
WM_BACKFILL_TARGET = int(os.environ.get("WEBMOTORS_BACKFILL_TARGET", "500"))
WM_BACKFILL_MAX_PAGES = int(os.environ.get("WEBMOTORS_BACKFILL_MAX_PAGES", "30"))

# Toggle either source independently (e.g. ENABLE_WEBMOTORS=0).
ENABLE_OLX = os.environ.get("ENABLE_OLX", "1") != "0"
ENABLE_WEBMOTORS = os.environ.get("ENABLE_WEBMOTORS", "1") != "0"


def gentle_sleep(base_seconds):
    """Sleep `base_seconds` ± up to 25% jitter. Fixed intervals are an easy bot
    tell; randomizing the spacing looks more human and lowers the ban risk."""
    time.sleep(base_seconds * random.uniform(0.75, 1.25))


def get_collection():
    client = MongoClient(connectionString)
    return client[databaseName][collectionName]


def olx_count(collection):
    # Legacy OLX records have no `source`; count everything that isn't Webmotors.
    return collection.count_documents({"source": {"$ne": "webmotors"}})


def wm_count(collection):
    return collection.count_documents({"source": "webmotors"})


def upsert_cars(collection, cars, page):
    """Upsert ads by their unique ad id. Works for either source: OLX ids are
    numeric, Webmotors ids are namespaced ('wm-<id>'), so they never collide.
    Returns how many existing ads were updated (already-known ads re-seen)."""
    if not cars:
        logging.info("No cars to upsert on page %s", page)
        return 0

    operations = []
    for car in cars:
        # Key on adId (the stable ad identity); fall back to the link, then the
        # title, only if the id couldn't be extracted. The title alone collides
        # across distinct ads, which falsely flags fresh ads as duplicates.
        key = ({"adId": car["adId"]} if car.get("adId")
               else {"link": car["link"]} if car.get("link")
               else {"announceName": car["announceName"]})
        created = car.pop("created", None)
        update = {"$set": car}
        if created is not None:
            update["$setOnInsert"] = {"created": created}
        operations.append(UpdateOne(key, update, upsert=True))
    result = collection.bulk_write(operations)

    if result.modified_count > 0:
        logging.info("%d duplicates (updates) on page %s",
                     result.modified_count, page)
    else:
        logging.info("Zero duplicates on page %s", page)

    return result.modified_count


# -----------------------------------------------------------------------------
# OLX worker
# -----------------------------------------------------------------------------
def olx_crawl_cycle(collection):
    """Walk pages until we hit known ads (duplicates) or the page limit."""
    driver = olxCore.configure_driver()
    try:
        page = 1
        duplicates = 0
        while duplicates == 0 and page < pageLimit:
            cars = olxCore.getCars(driver, "", page)
            duplicates = upsert_cars(collection, cars, page)
            logging.info("######### OLX page %s done #########", page)
            page += 1
            if duplicates == 0 and page < pageLimit:
                time.sleep(PAGE_DELAY_SECONDS)
    finally:
        driver.quit()


def olx_backfill_cycle(collection):
    """DB nearly empty: keep crawling until OLX holds BACKFILL_TARGET records
    (or we run out of pages / hit the safety cap)."""
    driver = olxCore.configure_driver()
    try:
        page = 1
        while page <= BACKFILL_MAX_PAGES:
            if olx_count(collection) >= BACKFILL_TARGET:
                logging.info("OLX backfill done (target %d)", BACKFILL_TARGET)
                break
            cars = olxCore.getCars(driver, "", page)
            if not cars:
                logging.info("OLX backfill: no cars on page %s; stopping", page)
                break
            upsert_cars(collection, cars, page)
            logging.info("OLX backfill: %d records after page %s",
                         olx_count(collection), page)
            page += 1
            if olx_count(collection) < BACKFILL_TARGET and page <= BACKFILL_MAX_PAGES:
                time.sleep(PAGE_DELAY_SECONDS)
    finally:
        driver.quit()


def olx_worker(collection):
    while True:
        try:
            count = olx_count(collection)
            if count < BACKFILL_THRESHOLD:
                logging.info("OLX: only %d records (< %d): backfilling toward %d",
                             count, BACKFILL_THRESHOLD, BACKFILL_TARGET)
                olx_backfill_cycle(collection)
            else:
                logging.info("OLX crawl cycle started (%d records)", count)
                olx_crawl_cycle(collection)
        except Exception as exc:
            logging.exception("OLX cycle failed: %s", exc)
        logging.info("OLX cycle finished, sleeping at %s", datetime.now())
        time.sleep(CYCLE_DELAY_SECONDS)


# -----------------------------------------------------------------------------
# Webmotors worker (undetected-chromedriver; gentle pacing to avoid an IP ban)
# -----------------------------------------------------------------------------
def webmotors_cycle(collection):
    """One Webmotors pass. In backfill mode (few records) it ignores the
    duplicate-stop and walks up to BACKFILL_MAX_PAGES toward the target; in
    normal mode it stops as soon as a page is all already-known ads."""
    backfill = wm_count(collection) < WM_BACKFILL_THRESHOLD
    page_cap = WM_BACKFILL_MAX_PAGES if backfill else WM_PAGE_LIMIT
    if backfill:
        logging.info("Webmotors: only %d records (< %d): backfilling toward %d",
                     wm_count(collection), WM_BACKFILL_THRESHOLD, WM_BACKFILL_TARGET)

    driver = wmCore.configure_driver()
    try:
        page = 1
        while page <= page_cap:
            if backfill and wm_count(collection) >= WM_BACKFILL_TARGET:
                logging.info("Webmotors backfill done (target %d)", WM_BACKFILL_TARGET)
                break
            cars = wmCore.getCars(driver, page)
            if not cars:
                logging.info("Webmotors: no cars on page %s; stopping", page)
                break
            duplicates = upsert_cars(collection, cars, page)
            logging.info("######### Webmotors page %s done (%d records) #########",
                         page, wm_count(collection))
            page += 1
            if not backfill and duplicates > 0:
                break  # caught up to known ads; nothing new beyond here
            if page <= page_cap:
                gentle_sleep(WM_PAGE_DELAY)  # be gentle: PerimeterX bans on volume
    finally:
        wmCore.quit_driver(driver)


def webmotors_worker(collection):
    while True:
        try:
            logging.info("Webmotors cycle started (%d records)", wm_count(collection))
            webmotors_cycle(collection)
        except Exception as exc:
            logging.exception("Webmotors cycle failed: %s", exc)
        logging.info("Webmotors cycle finished, sleeping at %s", datetime.now())
        time.sleep(WM_CYCLE_DELAY)


if __name__ == "__main__":
    collection = get_collection()

    workers = []
    if ENABLE_OLX:
        workers.append(threading.Thread(target=olx_worker, args=(collection,),
                                        name="OLX", daemon=True))
    if ENABLE_WEBMOTORS:
        workers.append(threading.Thread(target=webmotors_worker, args=(collection,),
                                        name="Webmotors", daemon=True))

    if not workers:
        raise SystemExit("Both sources disabled (ENABLE_OLX / ENABLE_WEBMOTORS).")

    logging.info("Starting %d crawler thread(s): %s",
                 len(workers), ", ".join(w.name for w in workers))
    for w in workers:
        w.start()
    # Keep the main thread alive; daemon workers loop forever.
    for w in workers:
        w.join()
