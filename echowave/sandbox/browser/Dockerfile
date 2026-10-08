# The browser box: browser-use driving Chromium for one person's task.
# Started by the sandbox service (POST /browsers), one container per task,
# unprivileged, every capability dropped, read-only root, profile on tmpfs.
# See README.md and ../server.py.
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    ANONYMIZED_TELEMETRY=false \
    BROWSER_USE_SETUP_LOGGING=false

COPY requirements.txt /app/requirements.txt
RUN pip install --break-system-packages -r /app/requirements.txt \
    # One stable path to the image's Chromium, whatever its build number.
    && ln -s "$(ls -d /ms-playwright/chromium-*/chrome-linux/chrome | head -n1)" /usr/local/bin/box-chromium \
    && test -x /usr/local/bin/box-chromium

ENV CHROME_PATH=/usr/local/bin/box-chromium
COPY box.py netguard.py /app/
WORKDIR /work
USER 1000:1000
CMD ["python3", "-u", "/app/box.py"]
