# wrangler (Cloudflare Pages deploys) needs Node 22. Install it in a Node image
# and copy the runtime over, so the final image stays the Python one.
FROM node:22-bookworm-slim AS wrangler
RUN npm install -g wrangler@4.147.0 && npm cache clean --force

FROM python:3.12-slim

# tini gives us correct signal handling, so a crawl stops cleanly on docker stop
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=wrangler /usr/local/bin/node /usr/local/bin/node
COPY --from=wrangler /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/wrangler/bin/wrangler.js /usr/local/bin/wrangler

WORKDIR /app
COPY pyproject.toml README.md ./
COPY apscrap ./apscrap
COPY sql ./sql
COPY site ./site
RUN pip install --no-cache-dir -e .

# A crawl writes nothing to disk but the site it builds in /tmp, so it has no
# business running as root.
RUN useradd --create-home --uid 10001 apscrap && chown -R apscrap:apscrap /app
USER apscrap

ENV PYTHONUNBUFFERED=1 \
    WRANGLER_SEND_METRICS=false
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["apscrap", "stats"]
