# Three-minute demo script

The point of the demo is one argument: **AI spend is not the token bill.** Every
step below exists to make that argument visible, in order.

Total wall time is about twelve minutes, of which three minutes are the actual
demo. Do the setup before anyone is watching.

---

## Before the demo (about 8 minutes, off camera)

```bash
cp .env.example .env          # set LITELLM_MASTER_KEY (must start with "sk-")
docker compose up -d --build
docker compose ps             # wait for postgres and redis to report healthy
```

Seed history so the dashboard opens with a trend rather than an empty grid. The
mock collectors backfill `DEMO_BACKFILL_DAYS` (14 by default) of compute and
vector DB spend on their first cycle, which takes a minute or two.

```bash
python mock_workloads/traffic_generator.py --count 400 --rate 8 --seed 42
```

Set a budget the demo can breach, sized just under what `research` is already
projected to spend:

```bash
curl -s http://localhost:8000/api/v1/forecast?team=research | jq '.projected_total'

curl -s -X POST http://localhost:8000/api/v1/budgets \
  -H 'Content-Type: application/json' \
  -d '{"team": "research", "period": "monthly", "limit_usd": 400, "alert_thresholds": [80, 100]}'
```

Confirm all three sources are present before going live:

```bash
docker compose exec -T postgres psql -U finops -d finopsai -c \
  "SELECT source, count(*), round(sum(amount_usd), 2) AS usd
     FROM finops.cost_record WHERE allocated = false GROUP BY source ORDER BY usd DESC;"
```

Open these tabs in order, so no clicking happens on camera:

1. <http://localhost:3000/d/finops-overview> - Grafana, anonymous, no login
2. <http://localhost:8000/docs> - the API
3. Slack channel receiving the webhook

---

## The demo (3 minutes)

### 0:00 - 0:30 | The number nobody has

Open on **Total AI spend this month**, then **Spend by source**.

> "Most teams can tell you their OpenAI bill. This is the same month's AI spend
> with the rest of it included: GPU compute, the vector database, the
> orchestration infrastructure. The token bill is the slice on the left. It's
> usually the smallest one."

Point at the donut. The compute slice being larger than the LLM slice is the
whole thesis in one panel.

### 0:30 - 1:15 | Who owns it

Move to **Spend by team**, then **Top 10 cost drivers**.

> "Every dollar is attributed to a team, an agent and a use case, because the
> proxy tags each call and the collectors carry those tags through. Research is
> expensive not because of tokens but because it fine-tunes -- you can see the
> GPU line dominate its bar."

On the driver table:

> "This is the actionable view: the specific model or resource, its owner, and
> what it cost. This is the row you take to the team that owns it."

### 1:15 - 1:50 | The tagging story

Move to **Unattributed spend (tagging debt)**.

> "About a tenth of spend arrives with no owner. That's not a bug in the
> pipeline - it's what actually happens: someone ships a service without tags.
> Most tools drop that spend or bury it. Showing it is the point. This line
> going down is the KPI for a FinOps rollout."

If asked how it is attributed anyway: shared cost that genuinely belongs to
nobody is split across teams by usage share, with the original record kept for
audit. See `docs/architecture/allocation.md`.

### 1:50 - 2:30 | The alert

In a terminal, start the runaway agent:

```bash
python mock_workloads/traffic_generator.py --burst research/experiment-planner --count 300 --rate 20
```

> "An agent has gone into a loop - a retry storm, a bad prompt template,
> whatever. Watch the budget gauge."

Show **Budget utilisation by team** climbing, then switch to Slack.

> "The evaluator runs every fifteen minutes. It fires once per threshold, so
> 80% doesn't repeat every cycle, and 100% escalates because that's genuinely
> new. The alert carries the forecast: at this run rate the budget is exhausted
> on the 24th. That's the difference between a dashboard and something that
> wakes someone up."

If the demo cannot wait fifteen minutes, force a cycle:

```bash
docker compose exec -T collectors python -c "
import asyncio
from finopsai.collectors.__main__ import build_evaluator
from finopsai.config import get_settings
print(asyncio.run(build_evaluator(get_settings()).run_once()))
"
```

### 2:30 - 3:00 | Where it is going

Close on **Forecast: projected month end by team** and **Cost per 1K tokens**.

> "Straight-line projection from the run rate - deliberately simple, and the API
> says so rather than implying precision it doesn't have. And efficiency
> separately from volume: a model can be cheap per token and still dominate the
> bill."

Optional closer, at <http://localhost:8000/docs>:

> "All of it is a documented API, so the dashboard is one consumer rather than
> the product."

---

## Recovery

| Symptom | Fix |
|---|---|
| Grafana panels empty | The Postgres datasource needs `POSTGRES_USER` / `POSTGRES_PASSWORD` in the environment. Check `docker compose logs grafana`. |
| No LLM spend, only compute and vector DB | Mock model responses may record zero cost. Re-run the generator with `--mode live` and a provider key set. |
| No Slack message | `SLACK_WEBHOOK_URL` unset - the evaluator logs `alert_not_delivered_no_notifier_configured` rather than failing. Check `docker compose logs collectors`. |
| Budget gauge missing | The gauge only appears after the evaluator's first cycle. Force one with the snippet above. |
| Dashboard has no history | Mock collectors backfill on their first run; give them a cycle, or raise `DEMO_BACKFILL_DAYS`. |

## Reset between runs

```bash
docker compose down -v && docker compose up -d --build
```

This deletes the Postgres volume, which is also the only way to re-run the
database init script that creates the `finopsai` database.
