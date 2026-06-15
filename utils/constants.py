# -*- coding: utf-8 -*-
# Requires Google Chrome installed. Selenium Manager resolves the driver.

import os

connectionString = os.environ.get("MONGO_URI", "mongodb://localhost:27017")

databaseName = "py"

collectionName = "cars"

pageLimit = int(os.environ.get("PAGE_LIMIT", "10"))

# Ads priced at or above this (in BRL) are ignored as outliers / data noise.
PRICE_CEILING = int(os.environ.get("PRICE_CEILING", "300000"))

monthsDictionary = {
    "jan": "January",
    "fev": "February",
    "mar": "March",
    "abr": "April",
    "mai": "May",
    "jun": "June",
    "jul": "July",
    "ago": "August",
    "set": "September",
    "out": "October",
    "nov": "November",
    "dez": "December"
}


def formattedURL(carBrand, page):
    return f"https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios/estado-pe?o={page}&q={carBrand}"
