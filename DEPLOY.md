# Running in production

apScrap runs on beyou-server's k3s cluster, deployed by Flux from
[be-infra](https://github.com/AndDev741/be-infra). Nothing about production is
configured in this repository except the image.

| Piece | Where |
|---|---|
| Image | `ghcr.io/anddev741/apscrap`, built by `.github/workflows/image.yml` on every push to `main` |
| Nightly job | be-infra `apps/apscrap/cronjob.yaml`, 04:20 Europe/Lisbon plus up to 20 minutes of jitter |
| Database | `apscrap` in the shared CloudNativePG cluster (be-infra `data/postgres.yaml`) |
| Backup | be-infra `apps/apscrap/backup.yaml`, 06:10, restic, 7 daily / 4 weekly / 12 monthly |
| Secrets | be-infra `apps/apscrap/secrets/*.sops.yaml`, encrypted with the cluster's age key |
| Site | Cloudflare Pages project `apscrap`, behind Cloudflare Access |
| Failure alert | Grafana's "Pod stuck" rule emails when the Job's pod stays Failed for 10 minutes |

## Shipping a change

Push to `main`. CI builds and pushes `latest`; Flux image automation commits the
new digest into be-infra within about five minutes; the next night's Job runs
it. The Job runs `apscrap migrate` before anything else, so schema and view
changes apply themselves.

## Operating it

```bash
ssh andre@beyou-server

# did last night's run work
kubectl -n apscrap get jobs
kubectl -n apscrap logs job/<name>

# run one now (the jitter still applies; up to 20 minutes before it starts)
kubectl -n apscrap create job --from=cronjob/apscrap-daily apscrap-manual-$(date +%s)

# what is in the warehouse
kubectl -n data exec -it pg-1 -c postgres -- psql -d apscrap -c "SELECT count(*) FROM listings"
```

## Loading the commute grid

The grid file reveals where you work, so it never goes through git:

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
