# Running in production

apScrap runs on beyou-server's k3s cluster, deployed by Flux from
[be-infra](https://github.com/AndDev741/be-infra). Nothing about production is
configured in this repository except the image.

| Piece | Where |
|---|---|
| Image | `ghcr.io/anddev741/apscrap`, built by `.github/workflows/image.yml` on every push to `main` |
| Nightly job | be-infra `apps/apscrap/cronjob.yaml`, 04:20 Europe/Lisbon plus up to 20 minutes of jitter: Imovirtual, then relist, lifecycle, dedup and the read models for every portal |
| Other portals | be-infra `apps/apscrap/sources.yaml`, CronJob `apscrap-sources`, 00:30 Europe/Lisbon plus jitter: RE/MAX, KW, ERA, then Casa Sapo (its robots.txt asks for 23:00 to 11:00 UTC). Done well before 04:20, which picks up what it parsed |
| Database | `apscrap` in the shared CloudNativePG cluster (be-infra `data/postgres.yaml`) |
| Backup | be-infra `apps/apscrap/backup.yaml`, 06:10, restic, 7 daily / 4 weekly / 12 monthly |
| Secrets | be-infra `apps/apscrap/secrets/*.sops.yaml`, encrypted with the cluster's age key |
| App | be-infra `apps/apscrap/web.yaml`: Deployment `apscrap-web` (`apscrap serve`), Ingress `apscrap.beyouweb.com`, behind Cloudflare Access |
| Assistant | OmniRoute, the same combo beyou uses (`apscrap-llm` secret); SQL as role `apscrap_readonly` (CNPG, `data/postgres.yaml`) |
| Failure alert | Grafana's "CronJob failed" rule emails when a CronJob's last run ended without success, and clears itself once the next run succeeds. Failed Job pods stay behind for their logs (three per CronJob); "Pod stuck" ignores them |

## Shipping a change

Push to `main`. CI builds and pushes `latest`; Flux image automation commits the
new digest into be-infra within about five minutes; the next night's Job runs
it. The Job runs `apscrap migrate` before anything else, so schema and view
changes apply themselves.

## The app

`apscrap-web` runs `apscrap migrate` and then `apscrap serve` on port 8080. It
reaches the outside world only through Traefik and the Cloudflare Tunnel, and
it trusts nobody it cannot identify: every API call checks Cloudflare Access's
JWT (`CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD` in the optional `apscrap-access`
secret) and answers 503 until that secret exists. Health checks
(`/api/health`) answer without it.

Putting it on the internet takes three things in Cloudflare, once:

1. A public hostname on the tunnel: `apscrap.beyouweb.com` to Traefik, the
   same service as the other apps, and the DNS CNAME pointing at the tunnel
   (it used to point at Cloudflare Pages).
2. The Access application on that hostname ("Casa & Carro (apScrap)", which
   also covers apscrap.pages.dev) with the reusable policy "Casa & Carro"
   listing both emails, plus a bypass for `/api/health` so checks need no
   login. Copy its audience tag.
3. The secret, in be-infra:

   ```bash
   cat > apps/apscrap/secrets/apscrap-access.sops.yaml <<YAML
   apiVersion: v1
   kind: Secret
   metadata: {name: apscrap-access, namespace: apscrap}
   type: Opaque
   stringData:
     CF_ACCESS_TEAM_DOMAIN: <team>.cloudflareaccess.com
     CF_ACCESS_AUD: <audience tag>
   YAML
   sops -e -i apps/apscrap/secrets/apscrap-access.sops.yaml
   # add it to apps/apscrap/kustomization.yaml, commit, push
   ```

The first email to sign in takes person A, the second person B. Names, the
second email, workplaces and every search criterion live in the app's
Definições page, in the database.

Everything the two of them create (votes, notes, photos, saved searches) is in
the `apscrap` database, so the nightly backup already covers it.

## Operating it

```bash
ssh andre@beyou-server

# did last night's run work
kubectl -n apscrap get jobs
kubectl -n apscrap logs job/<name>

# run one now (the jitter still applies; up to 20 minutes before it starts)
kubectl -n apscrap create job --from=cronjob/apscrap-daily apscrap-manual-$(date +%s)
# the other portals (Casa Sapo skips itself outside 23:00-11:00 UTC)
kubectl -n apscrap create job --from=cronjob/apscrap-sources apscrap-sources-$(date +%s)

# what is in the warehouse
kubectl -n data exec -it pg-1 -c postgres -- psql -d apscrap -c "SELECT count(*) FROM listings"
```

## Loading the commute grid

The grid file reveals where someone works, so it never goes through git. The
simplest way in is a tunnel to the database and `apscrap commute-load`:

```bash
# terminal 1: forward the cluster's Postgres to the laptop
ssh -L 15432:localhost:15432 andre@beyou-server \
    kubectl -n data port-forward svc/pg-rw 15432:5432
# terminal 2: load one grid per person and mode
PGHOST=localhost PGPORT=15432 PGUSER=apscrap PGPASSWORD=... \
    .venv/bin/apscrap commute-load ~/apscrap-commute/b.transit.commute.csv --slot b
```

The single-grid recipe below still works for person A's transit grid:

```bash
# on the laptop, after scripts/commute_grid.py
python3 - <<'EOF' > /tmp/commute.sql
import sys
lines = open("/path/to/your.commute.csv").read().splitlines()
meta = dict(kv.split("=", 1) for kv in lines[0][1:].strip().split(","))
print("BEGIN; TRUNCATE commute_cells;")
print("COPY commute_cells (lat_idx, lon_idx, minutes) FROM stdin WITH (FORMAT csv);")
print("\n".join(lines[2:])); print("\\.")
print(f"INSERT INTO commute_meta (id, label, step_lat, step_lon, departure) VALUES (1, '{meta['label']}', {meta['step_lat']}, {meta['step_lon']}, '{meta['departure']}') "
      "ON CONFLICT (id) DO UPDATE SET label=EXCLUDED.label, step_lat=EXCLUDED.step_lat, step_lon=EXCLUDED.step_lon, departure=EXCLUDED.departure, loaded_at=now();")
print("COMMIT;")
EOF
ssh andre@beyou-server 'kubectl -n data exec -i pg-1 -c postgres -- psql -d apscrap -U postgres' < /tmp/commute.sql
```

## The failure that matters

Everything is replaceable except the `apscrap` database. Re-crawling brings back
today's prices and nothing older, so check once a month that the backup is
still growing:

```bash
kubectl -n apscrap logs job/$(kubectl -n apscrap get jobs -o name | grep backup | tail -1 | cut -d/ -f2)
```

## Storage

Search payloads are trimmed before they are stored (one thumbnail, no photo
lists) and deleted after 60 days, which keeps `raw_pages` around 1 GB. The
snapshot table grows by roughly 21,000 rows a night with sales included, about
2 GB a year.
