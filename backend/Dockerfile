FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /srv

# Chromium and its OS libraries for the browser channel. Kept in its own early layer so that code
# changes do not re-download the browser. Version matches pyproject's playwright requirement.
RUN pip install --no-cache-dir "playwright>=1.48,<2" \
    && playwright install --with-deps chromium

COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
# Editable install keeps app/ resolved from /srv.
RUN pip install --no-cache-dir -e . \
    && useradd --system --uid 10001 onboarding \
    && mkdir -p /srv/artifacts \
    && chown onboarding /srv/artifacts
USER onboarding

EXPOSE 8200
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8200"]
