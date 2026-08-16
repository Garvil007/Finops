# Developer verification

End-to-end proof that the cost-capture layer works: a tagged chat completion
through the LiteLLM proxy lands in `LiteLLM_SpendLogs` with `request_tags`
populated.

Run every command from the repository root with `.env` in place
(`cp .env.example .env`, then set `LITELLM_MASTER_KEY`).

## 0. Record the proxy image

The stack defaults to `ghcr.io/berriai/litellm:main-stable`, a moving tag.
LiteLLM occasionally changes the SpendLogs schema between releases, so pin the
digest you verified against and set it in `.env` as `LITELLM_IMAGE`:

```bash
docker compose pull litellm-proxy
docker compose images litellm-proxy
```

## 1. Start the stack

```bash
docker compose up -d
docker compose ps
```

Expected: `postgres` and `redis` report `healthy`, `litellm-proxy` reports
`running`. The proxy runs its own Prisma migrations against the `litellm`
database on first boot; give it ~20s.

To reset from scratch — **this deletes all Postgres data**, including the
databases the init script creates:

```bash
docker compose down -v
```

The init script at `docker/postgres/init/01-create-databases.sh` runs *only* on
an empty data volume. If the `finopsai` database is missing, that is why.

## 2. Proxy health

```bash
curl -sf http://localhost:4000/health/liveliness
```

Expected: `"I'm alive!"`. The authenticated `/health` endpoint additionally
reports per-model status:

```bash
curl -s http://localhost:4000/health \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY"
```

## 3. Apply FinOpsAI migrations

```bash
alembic upgrade head
```

Expected: the `finops` schema exists with `finops.alembic_version` at revision
`0001`.

```bash
docker compose exec -T postgres \
  psql -U "${POSTGRES_USER:-finops}" -d finopsai \
  -c '\dn' -c 'SELECT version_num FROM finops.alembic_version;'
```

## 4. Generate a virtual key for a team

```bash
curl -s -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "key_alias": "team-search",
        "models": ["mock-gpt", "gpt-4o-mini", "claude-haiku-4-5"],
        "metadata": {"team": "search"}
      }'
```

Expected: JSON containing `"key": "sk-..."`. Export it:

```bash
export TEAM_KEY=sk-...
```

## 5. Make one tagged chat completion

`request_tags` is populated from `metadata.tags` — a flat array of
`key:value` strings. Arbitrary metadata keys land in the metadata JSON instead
and are **not** attributable.

```bash
curl -s -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $TEAM_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "model": "mock-gpt",
        "messages": [{"role": "user", "content": "ping"}],
        "metadata": {
          "tags": ["team:search", "agent_id:demo", "use_case:rag", "project:demo"]
        }
      }'
```

Expected: a normal chat-completion response. Swap `mock-gpt` for `gpt-4o-mini`
or `claude-haiku-4-5` once a provider key is set in `.env`.

## 6. Confirm the spend log row

Spend logs are flushed asynchronously, so poll rather than querying once:

```bash
for i in $(seq 1 15); do
  docker compose exec -T postgres \
    psql -U "${POSTGRES_USER:-finops}" -d litellm -t -A -F'|' -c \
    'SELECT request_id, model, spend, total_tokens, request_tags
       FROM "LiteLLM_SpendLogs"
      ORDER BY "startTime" DESC
      LIMIT 5;' | tee /tmp/spendlogs.txt
  grep -q 'team:search' /tmp/spendlogs.txt && break
  sleep 2
done
```

**Pass condition:** at least one row whose `request_tags` contains
`team:search`. That row is the unit the attribution engine consumes.

The mock model may record `spend = 0` — assert on row existence and
`request_tags`, not on a non-zero cost. Cost only becomes meaningful with a
real provider key.

## 7. Confirm the collector ingested it

The collectors service applies migrations on start, then polls spend logs every
60 seconds.

```bash
docker compose logs --tail=20 collectors
```

Expected: a `collector_cycle_complete` JSON line with a non-zero `written`.

```bash
docker compose exec -T postgres   psql -U "${POSTGRES_USER:-finops}" -d finopsai -c   'SELECT source, team, agent_id, use_case, model, amount_usd, quantity
     FROM finops.cost_record
    ORDER BY occurred_at DESC
    LIMIT 5;'
```

**Pass condition:** a row with `team = search`, `agent_id = demo`,
`use_case = rag`. Re-run the query after the next cycle — the row count must not
grow, because the dedup key drops the re-read.

Scrape the collector's metrics:

```bash
curl -s http://localhost:9100/metrics | grep finopsai_collector
```

## 8. Generate a demo dataset

Simulated compute and vector DB spend is generated automatically by the
collectors service when `ENABLE_MOCK_COLLECTORS=true`, backfilling
`DEMO_BACKFILL_DAYS` of history on first run. For LLM traffic:

```bash
python mock_workloads/traffic_generator.py --count 200 --rate 5
```

Live providers instead of the offline model, with a hard ceiling:

```bash
python mock_workloads/traffic_generator.py --mode live --max-spend-usd 0.25
```

Simulate a runaway agent for the budget-alerting demo:

```bash
python mock_workloads/traffic_generator.py --burst research/experiment-planner
```

Then check the spread across all three sources:

```bash
docker compose exec -T postgres   psql -U "${POSTGRES_USER:-finops}" -d finopsai -c   'SELECT source, team, round(sum(amount_usd), 4) AS usd
     FROM finops.cost_record
    GROUP BY 1, 2
    ORDER BY usd DESC;'
```

**Pass condition:** `llm`, `compute` and `vectordb` rows all present, `research`
dominating compute, and a visible `unattributed` team.

> Simulated rows carry `raw->>'simulated' = 'true'` and a `mock-` dedup-key
> prefix. To see only measured spend:
> `WHERE raw->>'simulated' IS NULL`.

## 9. Tear down

```bash
docker compose down        # keeps data
docker compose down -v     # deletes data (see step 1)
```
