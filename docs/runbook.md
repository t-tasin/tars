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
