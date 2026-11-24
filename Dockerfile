FROM python:3.12-slim

# tini gives us correct signal handling, so a crawl stops cleanly on docker stop
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY apscrap ./apscrap
COPY sql ./sql
COPY site ./site
RUN pip install --no-cache-dir -e .

# A crawl writes nothing to disk, so it has no business running as root.
RUN useradd --create-home --uid 10001 apscrap && chown -R apscrap:apscrap /app
USER apscrap

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["apscrap", "stats"]
