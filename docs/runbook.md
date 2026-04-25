# Runbook

## Everyday Ops

### Deploy latest
```bash
curl -sf -X POST https://tars.<domain>/api/v1/deploy \
  -H "Authorization: Bearer $TARS_API_KEY" \
  -d '{"confirm": true}'
```
Or `/deploy` via Telegram.

### Restart a service
```bash
ssh node1 'docker compose -f /opt/tars/deploy/node1/docker-compose.yml restart tars-backend'
ssh node2 'systemctl restart llama-l2'
```

### Tail logs
```bash
ssh node1 'docker logs -f tars-backend'
ssh node2 'journalctl -u llama-l2 -f'
```

## Incidents

### Node 2 llama.cpp OOM
- Symptom: L2 service crashes, restart loop
- Check: `dmesg | grep -i 'out of memory'`
- Fix: reduce `--ctx-size` in systemd unit from 8192 → 4096, or drop `--parallel` from 2 → 1
- Long: verify Qdrant/Redis caps not exceeded; add swap (not preferred — slow)

### Postgres corruption / WAL full
- Symptom: `tars-backend` logs `asyncpg.exceptions.DiskFullError`
- Check: `df -h` on Node 1
- Fix: `docker compose exec tars-db pg_dump ...` → prune `pg_wal` or expand volume

### Redis queue stuck
- Symptom: coding jobs time out 5min
- Check: `redis-cli ZCARD tars:jobs:queue` (backlog), `SMEMBERS tars:jobs:processing` (stuck)
- Fix: `SREM tars:jobs:processing <stuck_id>` + manual re-enqueue or drop

### Claude unavailable
- Auto: fallback chain kicks in (Claude → Gemini Pro → local)
- If all AI down: raw-data delivery mode (HC-09)

### Public dashboard leaking anything suspicious
- Immediate: `docker compose stop cloudflared` on Node 2 (cuts public tunnel)
- Audit: `public_events` table, find offending event, update sanitizer, add to fuzz canary list
- Re-deploy when fuzz suite passes

### Power outage
- Boot order: Node 1 first (Postgres init), Node 2 second
- Health: hit `/api/v1/health` — wait `status=healthy` before considering up
- Cron catchup: scheduled jobs that missed during outage will fire at next cron slot (not backfilled)

## Backups

### Run manually
```bash
ssh node1 'bash /opt/tars/deploy/scripts/backup.sh'
```

### Restore
```bash
ssh node1 'bash /opt/tars/deploy/scripts/restore.sh <backup-file.age>'
```

Private key stored off-machine (1Password or encrypted USB).

## Monitoring
- Grafana: http://node1:3000 (local Tailscale)
- Public dashboard: https://tars.<domain>
- Prometheus: http://node1:9090
- Loki: TODO phase 6

## Emergency Shutdown
```bash
ssh node1 'docker compose down'
ssh node2 'docker compose down && systemctl stop llama-l1 llama-l2 llama-embed'
```
Tasin only. HC-03 blocks any TARS-initiated shutdown.

## Claude SSH Permission Boundary

Claude operates over SSH on `tars1` / `tars2`. Permission scope is intentionally narrow.

### What Claude can do without password
- `systemctl status <unit>` — any unit (default)
- `journalctl -u <unit>` — via `systemd-journal` group
- `docker ps` / `docker logs` / `docker inspect` — via `docker` group
- `sudo -n systemctl restart tars-backend` — via `/etc/sudoers.d/tars-claude` NOPASSWD
- `sudo -n systemctl reload tars-backend` — same

### What Claude cannot do
- `sudo systemctl stop|disable tars-backend` — password required → fails fast
- `sudo apt install|remove` — password required
- `sudo systemctl restart` on any other unit
- Anything destructive (rm, reboot, docker compose down, redis FLUSHALL)

Claude reports the password prompt to Tasin. Tasin decides: run manually, or scope a new sudoers entry.

### Sudoers content (Node 1)
```
tasin ALL=(root) NOPASSWD: /bin/systemctl restart tars-backend, /bin/systemctl reload tars-backend
```
File: `/etc/sudoers.d/tars-claude`, mode `0440`. Validate with `sudo visudo -c -f /etc/sudoers.d/tars-claude` before saving — bad sudoers = locked sudo = recovery mode.

### Boundary check (run as `tasin`, not via sudo)
```bash
sudo -n systemctl restart tars-backend   # expect: silent success
sudo -n systemctl stop tars-backend      # expect: "password required"
sudo -n apt-get install -y foo           # expect: "password required"
```
Stop / apt blocked = boundary correct.

### When to revisit
- **Phase 4** (autonomy triggers go live) — tighten before any agent gains `WRITE_INFRA`
- **Production** (real wiki, real Gmail tokens) — audit sudoers, consider yanking restart NOPASSWD entirely
- **Public dashboard live** — re-audit, HC-13 territory
