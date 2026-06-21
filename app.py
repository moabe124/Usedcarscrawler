import os
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template, request
from pymongo import MongoClient

from utils.constants import collectionName, databaseName, connectionString
from utils.ranking import rank_cars

# Brazil time (UTC-3), matching how the crawler stores postDate.
BR_TZ = ZoneInfo("Etc/GMT-3")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)
_client = MongoClient(connectionString)
_collection = _client[databaseName][collectionName]

# Indexes backing the /api/cars query: filter by postDate, sort by price.
# create_index is idempotent, so this is safe to run on every startup.
try:
    _collection.create_index("postDate")
    _collection.create_index("price")
except Exception as exc:
    logging.warning("Could not ensure indexes: %s", exc)


def serialize(car):
    """Make a Mongo document JSON-friendly (drop _id, stringify datetimes)."""
    car.pop("_id", None)
    for key in ("postDate", "created"):
        value = car.get(key)
        if value is not None and hasattr(value, "isoformat"):
            car[key] = value.isoformat()
    return car


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/cars")
def cars():
    brand = request.args.get("brand", "").strip()
    limit = min(int(request.args.get("limit", 5000)), 10000)
    # Only ads posted within the last `days` days. Default 14; 0/blank = no limit.
    days = request.args.get("days", "14").strip()

    query = {}
    if brand:
        # Case-insensitive match on the ad title.
        query["announceName"] = {"$regex": brand, "$options": "i"}
    if days:
        try:
            days_int = int(days)
        except ValueError:
            days_int = 0
        if days_int > 0:
            cutoff = datetime.now(BR_TZ) - timedelta(days=days_int)
            query["postDate"] = {"$gte": cutoff}

    cars_list = [serialize(car) for car in _collection.find(query).limit(limit)]

    # Score against peers from the WHOLE history of this brand (ignoring the days
    # window) so a recent ad is judged against all known prices, not just 14 days.
    ref_query = {"announceName": query["announceName"]} if brand else {}
    ref_pop = _collection.find(
        ref_query, {"announceName": 1, "year": 1, "price": 1, "kilometer": 1, "_id": 0})
    rank_cars(cars_list, reference_population=ref_pop)

    return jsonify(cars_list)


@app.get("/api/health")
def health():
    try:
        _client.admin.command("ping")
        return jsonify(status="ok", cars=_collection.estimated_document_count())
    except Exception as exc:
        return jsonify(status="error", detail=str(exc)), 503


if __name__ == "__main__":
    # Debugger off by default; enable with FLASK_DEBUG=1 for local development.
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=5000, debug=debug)
