FROM python:3.12-slim AS base

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 continuum && \
    useradd --uid 1000 --gid continuum --create-home continuum

WORKDIR /app
COPY continuum/ continuum/
COPY pyproject.toml .
COPY README.md .
RUN pip install --no-cache-dir . && \
    rm -rf /root/.cache

USER continuum
EXPOSE 7357

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7357/api/overview')" || exit 1

ENTRYPOINT ["continuum"]
CMD ["--help"]
