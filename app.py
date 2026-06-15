import logging

from flask import Flask, jsonify, render_template, request
from pymongo import MongoClient

from utils.constants import collectionName, databaseName, connectionString

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)
_client = MongoClient(connectionString)
_collection = _client[databaseName][collectionName]


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
    limit = min(int(request.args.get("limit", 500)), 2000)

    query = {}
    if brand:
        # Case-insensitive match on the ad title.
        query["announceName"] = {"$regex": brand, "$options": "i"}

    cursor = _collection.find(query).sort("price", 1).limit(limit)
    return jsonify([serialize(car) for car in cursor])


@app.get("/api/health")
def health():
    try:
        _client.admin.command("ping")
        return jsonify(status="ok", cars=_collection.estimated_document_count())
    except Exception as exc:
        return jsonify(status="error", detail=str(exc)), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
