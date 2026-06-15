FROM python:3.12-slim

WORKDIR /usedcarscrawler

# Install Google Chrome. Selenium Manager (bundled with Selenium 4.6+) resolves
# and downloads the matching chromedriver at runtime — no manual binary needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget gnupg ca-certificates fonts-liberation \
    && wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y --no-install-recommends ./google-chrome-stable_current_amd64.deb \
    && rm google-chrome-stable_current_amd64.deb \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV MONGO_URI=mongodb://localhost:27017

EXPOSE 5000

# Default command runs the web app; the crawler service overrides it in compose.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "app:app"]
