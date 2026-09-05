import os
import uuid
import decimal
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import duckdb
import uvicorn
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import DictLoader, Environment, select_autoescape

DUCKDB_PATH = os.getenv("DUCKDB_PATH", "serving/ops_demo.duckdb")
LOCAL_ACTOR = "local-analyst"

CASE_TYPES = (
    "MISMATCH_AMOUNT",
    "MISMATCH_STATUS",
    "MISMATCH_BOTH",
    "UNMATCHED_INTERNAL",
    "UNMATCHED_NETWORK",
)
CHANNELS = ("ATM", "POS", "ECOM", "WALLET", "IMPS")
ALLOWED_ACTIONS = {"approve", "resolve", "escalate"}
ALLOWED_SORTS = {"aging", "amount_diff"}

BASE_STYLE = """
:root {
    color-scheme: light;
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
    background: #f5f7fb;
    color: #172033;
}
* { box-sizing: border-box; }
body { margin: 0; background: #f5f7fb; }
header {
    background: #152238;
    color: white;
    padding: 1.1rem 2rem;
}
header a { color: white; text-decoration: none; }
main { max-width: 1400px; margin: 0 auto; padding: 1.5rem; }
h1, h2, h3 { margin-top: 0; }
.muted { color: #64748b; }
.kpis {
    display: grid;
    grid-template-columns: repeat(4, minmax(150px, 1fr));
    gap: 1rem;
    margin-bottom: 1.25rem;
}
.kpi, .panel {
    background: white;
    border: 1px solid #dce3ec;
    border-radius: 10px;
    box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04);
}
.kpi { padding: 1rem; }
.kpi .label {
    color: #64748b;
    font-size: 0.82rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
.kpi .value {
    display: block;
    margin-top: 0.35rem;
    font-size: 1.55rem;
    font-weight: 700;
}
.panel { padding: 1rem; margin-bottom: 1.25rem; }
.filters {
    display: flex;
    align-items: end;
    flex-wrap: wrap;
    gap: 0.8rem;
}
label {
    display: grid;
    gap: 0.35rem;
    color: #334155;
    font-size: 0.9rem;
}
select, input, textarea, button {
    font: inherit;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 0.55rem 0.7rem;
}
textarea { min-height: 100px; resize: vertical; }
button {
    border-color: #2563eb;
    background: #2563eb;
    color: white;
    cursor: pointer;
}
button:hover { background: #1d4ed8; }
table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
}
th, td {
    border-bottom: 1px solid #e2e8f0;
    padding: 0.7rem;
    text-align: left;
    vertical-align: top;
}
th {
    background: #f8fafc;
    color: #475569;
    white-space: nowrap;
}
tr:hover td { background: #f8fbff; }
td.numeric, th.numeric { text-align: right; font-variant-numeric: tabular-nums; }
.case-link { color: #1d4ed8; font-weight: 600; text-decoration: none; }
.case-link:hover { text-decoration: underline; }
.badge {
    display: inline-block;
    border-radius: 999px;
    padding: 0.2rem 0.55rem;
    font-size: 0.75rem;
    font-weight: 700;
}
.badge-open { background: #fef3c7; color: #92400e; }
.badge-manual_review { background: #fee2e2; color: #991b1b; }
.badge-auto_resolved { background: #dcfce7; color: #166534; }
.badge-closed { background: #e2e8f0; color: #334155; }
.comparison {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
}
.side {
    border: 1px solid #dce3ec;
    border-radius: 8px;
    padding: 1rem;
}
.field {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    border-bottom: 1px solid #eef2f7;
    padding: 0.5rem 0;
}
.field:last-child { border-bottom: 0; }
.history {
    display: grid;
    gap: 0.75rem;
}
.history-item {
    border-left: 4px solid #2563eb;
    background: #f8fafc;
    padding: 0.75rem;
}
.actions-form { display: grid; gap: 0.8rem; max-width: 650px; }
.back { display: inline-block; margin-bottom: 1rem; color: #1d4ed8; }
.empty { padding: 2rem; text-align: center; color: #64748b; }
@media (max-width: 800px) {
    .kpis { grid-template-columns: 1fr 1fr; }
    .comparison { grid-template-columns: 1fr; }
    .table-wrap { overflow-x: auto; }
}
"""

QUEUE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Payment Exception Ops Console</title>
    <style>{{ style }}</style>
</head>
<body>
<header>
    <a href="/"><h1>Payment Exception Ops Console</h1></a>
</header>
<main>
    <section class="kpis">
        <div class="kpi">
            <span class="label">Open</span>
            <span class="value">{{ kpis.open_count }}</span>
        </div>
        <div class="kpi">
            <span class="label">Manual review</span>
            <span class="value">{{ kpis.manual_review_count }}</span>
        </div>
        <div class="kpi">
            <span class="label">Auto resolved</span>
            <span class="value">{{ kpis.auto_resolved_count }}</span>
        </div>
        <div class="kpi">
            <span class="label">Absolute difference</span>
            <span class="value">₹{{ "%.2f"|format(kpis.sum_abs_amount_diff) }}</span>
        </div>
    </section>

    <section class="panel">
        <form method="get" action="/" class="filters">
            <label>
                Channel
                <select name="channel">
                    <option value="">All channels</option>
                    {% for item in channels %}
                    <option value="{{ item }}" {% if item == selected_channel %}selected{% endif %}>
                        {{ item }}
                    </option>
                    {% endfor %}
                </select>
            </label>

            <label>
                Case type
                <select name="case_type">
                    <option value="">All case types</option>
                    {% for item in case_types %}
                    <option value="{{ item }}" {% if item == selected_case_type %}selected{% endif %}>
                        {{ item }}
                    </option>
                    {% endfor %}
                </select>
            </label>

            <label>
                Sort
                <select name="sort">
                    <option value="aging" {% if selected_sort == "aging" %}selected{% endif %}>
                        Aging — oldest first
                    </option>
                    <option value="amount_diff" {% if selected_sort == "amount_diff" %}selected{% endif %}>
                        Absolute amount difference
                    </option>
                </select>
            </label>

            <button type="submit">Apply</button>
        </form>
    </section>

    <section class="panel table-wrap">
        <h2>Active exception queue</h2>
        {% if cases %}
        <table>
            <thead>
                <tr>
                    <th>Case ID</th>
                    <th>Business date</th>
                    <th>Channel</th>
                    <th>Case type</th>
                    <th class="numeric">Amount difference</th>
                    <th>Internal status</th>
                    <th>Network status</th>
                    <th class="numeric">Aging days</th>
                    <th>Effective status</th>
                </tr>
            </thead>
            <tbody>
                {% for case in cases %}
                <tr>
                    <td>
                        <a class="case-link" href="/cases/{{ case.case_id }}">
                            {{ case.case_id }}
                        </a>
                    </td>
                    <td>{{ case.business_date }}</td>
                    <td>{{ case.channel }}</td>
                    <td>{{ case.case_type }}</td>
                    <td class="numeric">
                        {% if case.amount_diff is not none %}
                            ₹{{ "%+.2f"|format(case.amount_diff) }}
                        {% else %}
                            —
                        {% endif %}
                    </td>
                    <td>{{ case.internal_status or "MISSING" }}</td>
                    <td>{{ case.network_status or "MISSING" }}</td>
                    <td class="numeric">{{ case.aging_days }}</td>
                    <td>
                        <span class="badge badge-{{ case.effective_status|lower }}">
                            {{ case.effective_status }}
                        </span>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <div class="empty">No active cases match the selected filters.</div>
        {% endif %}
    </section>
</main>
</body>
</html>
"""

DETAIL_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ case.case_id }} — Payment Exception Ops Console</title>
    <style>{{ style }}</style>
</head>
<body>
<header>
    <a href="/"><h1>Payment Exception Ops Console</h1></a>
</header>
<main>
    <a class="back" href="/">← Back to queue</a>

    <section class="panel">
        <h2>{{ case.case_id }}</h2>
        <p class="muted">
            {{ case.case_type }} · {{ case.channel }} · {{ case.business_date }}
        </p>
        <p>
            <span class="badge badge-{{ case.effective_status|lower }}">
                {{ case.effective_status }}
            </span>
            {% if case.disposition %}
            <span class="badge badge-{{ case.disposition|lower }}">
                {{ case.disposition }}
            </span>
            {% endif %}
        </p>

        <div class="field">
            <strong>Transaction</strong>
            <span>{{ case.txn_id }}</span>
        </div>
        <div class="field">
            <strong>Amount difference</strong>
            <span>
                {% if case.amount_diff is not none %}
                    ₹{{ "%+.2f"|format(case.amount_diff) }}
                {% else %}
                    —
                {% endif %}
            </span>
        </div>
        <div class="field">
            <strong>First seen</strong>
            <span>{{ case.first_seen_ts }}</span>
        </div>
        <div class="field">
            <strong>Last updated</strong>
            <span>{{ case.last_updated_ts }}</span>
        </div>
        <div class="field">
            <strong>Reason</strong>
            <span>{{ case.reason }}</span>
        </div>
    </section>

    <section class="panel">
        <h2>Internal vs network</h2>
        <div class="comparison">
            <div class="side">
                <h3>Internal ledger</h3>
                <div class="field">
                    <strong>Amount</strong>
                    <span>
                        {% if case.internal_amount is not none %}
                            ₹{{ "%.2f"|format(case.internal_amount) }}
                        {% else %}
                            MISSING
                        {% endif %}
                    </span>
                </div>
                <div class="field">
                    <strong>Status</strong>
                    <span>{{ case.internal_status or "MISSING" }}</span>
                </div>
            </div>

            <div class="side">
                <h3>Network feed</h3>
                <div class="field">
                    <strong>Amount</strong>
                    <span>
                        {% if case.network_amount is not none %}
                            ₹{{ "%.2f"|format(case.network_amount) }}
                        {% else %}
                            MISSING
                        {% endif %}
                    </span>
                </div>
                <div class="field">
                    <strong>Status</strong>
                    <span>{{ case.network_status or "MISSING" }}</span>
                </div>
            </div>
        </div>
    </section>

    <section class="panel">
        <h2>Disposition</h2>
        <form
            class="actions-form"
            method="post"
            action="/cases/{{ case.case_id }}/disposition"
        >
            <label>
                Action
                <select name="action" required>
                    <option value="approve">Approve and close</option>
                    <option value="resolve">Resolve and close</option>
                    <option value="escalate">Escalate for manual review</option>
                </select>
            </label>

            <label>
                Note
                <textarea
                    name="note"
                    maxlength="2000"
                    placeholder="Add an investigation or resolution note"
                ></textarea>
            </label>

            <input
                type="hidden"
                name="idempotency_key"
                value="{{ idempotency_key }}"
            >
            <button type="submit">Record disposition</button>
        </form>
    </section>

    <section class="panel">
        <h2>Action history</h2>
        {% if history %}
        <div class="history">
            {% for item in history %}
            <div class="history-item">
                <strong>{{ item.action|upper }}</strong>
                <div class="muted">
                    {{ item.actor }} · {{ item.action_ts }}
                </div>
                {% if item.note %}
                <p>{{ item.note }}</p>
                {% endif %}
            </div>
            {% endfor %}
        </div>
        {% else %}
        <div class="empty">No analyst actions have been recorded.</div>
        {% endif %}
    </section>
</main>
</body>
</html>
"""

templates = Environment(
    loader=DictLoader(
        {
            "queue.html": QUEUE_TEMPLATE,
            "detail.html": DETAIL_TEMPLATE,
        }
    ),
    autoescape=select_autoescape(default=True),
)


@contextmanager
def database() -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect(DUCKDB_PATH)
    try:
        yield connection
    finally:
        connection.close()


def rows_as_dicts(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    parameters: list[Any] | None = None,
) -> list[dict[str, Any]]:
    cursor = connection.execute(sql, parameters or [])
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def one_as_dict(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    parameters: list[Any] | None = None,
) -> dict[str, Any] | None:
    rows = rows_as_dicts(connection, sql, parameters)
    return rows[0] if rows else None


def initialize_database() -> None:
    database_parent = Path(DUCKDB_PATH).expanduser().resolve().parent
    database_parent.mkdir(parents=True, exist_ok=True)

    with database() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS gold_exception_cases (
                case_key VARCHAR,
                case_id VARCHAR,
                txn_id VARCHAR,
                business_date DATE,
                channel VARCHAR,
                case_type VARCHAR,
                internal_amount DECIMAL(18,2),
                network_amount DECIMAL(18,2),
                amount_diff DECIMAL(18,2),
                internal_status VARCHAR,
                network_status VARCHAR,
                disposition VARCHAR,
                reason VARCHAR,
                status VARCHAR,
                first_seen_ts TIMESTAMP,
                last_updated_ts TIMESTAMP
            )
            """)

        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_gold_exception_case_id
            ON gold_exception_cases(case_id)
            """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS ops_case_actions (
                action_id VARCHAR,
                case_id VARCHAR,
                action VARCHAR,
                note VARCHAR,
                actor VARCHAR,
                idempotency_key VARCHAR UNIQUE,
                action_ts TIMESTAMP
            )
            """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS ops_case_state (
                case_id VARCHAR PRIMARY KEY,
                status VARCHAR,
                last_action VARCHAR,
                last_actor VARCHAR,
                updated_ts TIMESTAMP
            )
            """)

        existing_count = connection.execute("SELECT COUNT(*) FROM gold_exception_cases").fetchone()[
            0
        ]

        if existing_count == 0:
            seed_cases(connection)


def seed_cases(connection: duckdb.DuckDBPyConnection) -> None:
    """Seed 60 deterministic cases when no read-model data exists."""
    base_now = datetime.now(timezone.utc).replace(
        hour=12,
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=None,
    )

    rows: list[tuple[Any, ...]] = []

    for index in range(60):
        sequence = index + 1
        case_type = CASE_TYPES[index % len(CASE_TYPES)]
        channel = CHANNELS[(index * 3) % len(CHANNELS)]
        business_date = date.today() - timedelta(days=index % 10)
        first_seen_ts = base_now - timedelta(
            days=index % 10,
            hours=(index * 7) % 24,
        )
        last_updated_ts = first_seen_ts + timedelta(hours=2 + (index % 8))

        txn_id = f"TXN{sequence:012d}"
        case_key = f"SEED-{business_date.isoformat()}-{txn_id}"
        case_id = f"CASE-{business_date.isoformat()}-{sequence:08d}"

        base_amount = decimal.Decimal(f"{125.0 + (index * 83.17) % 4800.0:.2f}")
        internal_amount: decimal.Decimal | None = base_amount
        network_amount: decimal.Decimal | None = base_amount
        amount_diff: decimal.Decimal | None = decimal.Decimal("0.00")
        internal_status: str | None = ("SETTLED", "PENDING", "FAILED", "REVERSED")[index % 4]
        network_status: str | None = internal_status
        disposition = "MANUAL"
        status = "OPEN"

        if case_type == "MISMATCH_AMOUNT":
            # Alternate sub-rupee AUTO cases and larger MANUAL cases.
            if (index // len(CASE_TYPES)) % 2 == 0:
                amount_diff = decimal.Decimal("0.50")
                disposition = "AUTO"
                status = "AUTO_RESOLVED"
            else:
                amount_diff = decimal.Decimal(f"{2.25 + (index % 9) * 1.35:.2f}")
            network_amount = base_amount - amount_diff
            reason = f"Amount differs by ₹{amount_diff:.2f}"

        elif case_type == "MISMATCH_STATUS":
            network_status = {
                "SETTLED": "PENDING",
                "PENDING": "FAILED",
                "FAILED": "REVERSED",
                "REVERSED": "SETTLED",
            }[internal_status]
            reason = f"Status differs: {internal_status} vs {network_status}"

        elif case_type == "MISMATCH_BOTH":
            amount_diff = decimal.Decimal(f"{3.50 + (index % 7) * 2.10:.2f}")
            network_amount = base_amount - amount_diff
            network_status = {
                "SETTLED": "PENDING",
                "PENDING": "FAILED",
                "FAILED": "REVERSED",
                "REVERSED": "SETTLED",
            }[internal_status]
            reason = (
                f"Amount differs by ₹{amount_diff:.2f} and status differs: "
                f"{internal_status} vs {network_status}"
            )

        elif case_type == "UNMATCHED_INTERNAL":
            network_amount = None
            network_status = None
            amount_diff = None
            reason = "Transaction exists internally but is missing from the network feed"

        else:
            internal_amount = None
            internal_status = None
            network_amount = base_amount
            network_status = ("SETTLED", "PENDING", "FAILED", "REVERSED")[(index + 1) % 4]
            amount_diff = None
            reason = "Transaction exists in the network feed but is missing internally"

        rows.append(
            (
                case_key,
                case_id,
                txn_id,
                business_date,
                channel,
                case_type,
                internal_amount,
                network_amount,
                amount_diff,
                internal_status,
                network_status,
                disposition,
                reason,
                status,
                first_seen_ts,
                last_updated_ts,
            )
        )

    connection.executemany(
        """
        INSERT INTO gold_exception_cases (
            case_key,
            case_id,
            txn_id,
            business_date,
            channel,
            case_type,
            internal_amount,
            network_amount,
            amount_diff,
            internal_status,
            network_status,
            disposition,
            reason,
            status,
            first_seen_ts,
            last_updated_ts
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


EFFECTIVE_CASES_SQL = """
    SELECT
        g.case_key,
        g.case_id,
        g.txn_id,
        g.business_date,
        g.channel,
        g.case_type,
        g.internal_amount,
        g.network_amount,
        g.amount_diff,
        g.internal_status,
        g.network_status,
        g.disposition,
        g.reason,
        g.status AS source_status,
        g.first_seen_ts,
        g.last_updated_ts,
        COALESCE(s.status, g.status) AS effective_status,
        GREATEST(
            0,
            CAST(
                DATE_DIFF(
                    'day',
                    CAST(g.first_seen_ts AS DATE),
                    CURRENT_DATE
                ) AS BIGINT
            )
        ) AS aging_days
    FROM gold_exception_cases AS g
    LEFT JOIN ops_case_state AS s
        ON s.case_id = g.case_id
"""


def get_kpis(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    row = one_as_dict(
        connection,
        """
        WITH effective AS (
            SELECT
                COALESCE(s.status, g.status) AS effective_status,
                g.amount_diff
            FROM gold_exception_cases AS g
            LEFT JOIN ops_case_state AS s
                ON s.case_id = g.case_id
        )
        SELECT
            COUNT(*) FILTER (
                WHERE effective_status = 'OPEN'
            ) AS open_count,
            COUNT(*) FILTER (
                WHERE effective_status = 'MANUAL_REVIEW'
            ) AS manual_review_count,
            COUNT(*) FILTER (
                WHERE effective_status = 'AUTO_RESOLVED'
            ) AS auto_resolved_count,
            COALESCE(SUM(ABS(amount_diff)), 0.0) AS sum_abs_amount_diff
        FROM effective
        """,
    )

    return row or {
        "open_count": 0,
        "manual_review_count": 0,
        "auto_resolved_count": 0,
        "sum_abs_amount_diff": 0.0,
    }


def get_active_cases(
    connection: duckdb.DuckDBPyConnection,
    sort: str,
    channel: str | None,
    case_type: str | None,
) -> list[dict[str, Any]]:
    filters = ["effective_status IN ('OPEN', 'MANUAL_REVIEW')"]
    parameters: list[Any] = []

    if channel:
        filters.append("channel = ?")
        parameters.append(channel)

    if case_type:
        filters.append("case_type = ?")
        parameters.append(case_type)

    order_by = (
        "ABS(COALESCE(amount_diff, 0.0)) DESC, aging_days DESC, case_id"
        if sort == "amount_diff"
        else "aging_days DESC, first_seen_ts ASC, case_id"
    )

    return rows_as_dicts(
        connection,
        f"""
        WITH effective AS (
            {EFFECTIVE_CASES_SQL}
        )
        SELECT *
        FROM effective
        WHERE {" AND ".join(filters)}
        ORDER BY {order_by}
        """,
        parameters,
    )


app = FastAPI(
    title="Payment Exception Ops Console",
    description="Offline DuckDB-backed payment exception operations console.",
)


@app.on_event("startup")
def startup() -> None:
    initialize_database()


@app.get("/", response_class=HTMLResponse)
def queue_page(
    request: Request,
    sort: str = Query(default="aging"),
    channel: str | None = Query(default=None),
    case_type: str | None = Query(default=None),
) -> HTMLResponse:
    if sort not in ALLOWED_SORTS:
        raise HTTPException(status_code=400, detail="sort must be aging or amount_diff")
    if channel and channel not in CHANNELS:
        raise HTTPException(status_code=400, detail="Unknown channel")
    if case_type and case_type not in CASE_TYPES:
        raise HTTPException(status_code=400, detail="Unknown case type")

    with database() as connection:
        cases = get_active_cases(connection, sort, channel, case_type)
        kpis = get_kpis(connection)

    html = templates.get_template("queue.html").render(
        request=request,
        style=BASE_STYLE,
        cases=cases,
        kpis=kpis,
        channels=CHANNELS,
        case_types=CASE_TYPES,
        selected_channel=channel,
        selected_case_type=case_type,
        selected_sort=sort,
    )
    return HTMLResponse(html)


@app.get("/cases")
def cases_json(
    sort: str = Query(default="aging"),
    channel: str | None = Query(default=None),
    case_type: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    if sort not in ALLOWED_SORTS:
        raise HTTPException(status_code=400, detail="sort must be aging or amount_diff")
    if channel and channel not in CHANNELS:
        raise HTTPException(status_code=400, detail="Unknown channel")
    if case_type and case_type not in CASE_TYPES:
        raise HTTPException(status_code=400, detail="Unknown case type")

    with database() as connection:
        return get_active_cases(connection, sort, channel, case_type)


@app.get("/cases/{case_id}", response_class=HTMLResponse)
def case_detail(request: Request, case_id: str) -> HTMLResponse:
    with database() as connection:
        case = one_as_dict(
            connection,
            f"""
            WITH effective AS (
                {EFFECTIVE_CASES_SQL}
            )
            SELECT *
            FROM effective
            WHERE case_id = ?
            """,
            [case_id],
        )

        if case is None:
            raise HTTPException(status_code=404, detail="Case not found")

        history = rows_as_dicts(
            connection,
            """
            SELECT
                action_id,
                case_id,
                action,
                note,
                actor,
                idempotency_key,
                action_ts
            FROM ops_case_actions
            WHERE case_id = ?
            ORDER BY action_ts DESC, action_id DESC
            """,
            [case_id],
        )

    html = templates.get_template("detail.html").render(
        request=request,
        style=BASE_STYLE,
        case=case,
        history=history,
        idempotency_key=str(uuid.uuid4()),
    )
    return HTMLResponse(html)


@app.post("/cases/{case_id}/disposition")
def record_disposition(
    case_id: str,
    action: str = Form(...),
    note: str = Form(default=""),
    idempotency_key: str = Form(...),
) -> RedirectResponse:
    normalized_action = action.strip().lower()
    normalized_note = note.strip()
    normalized_idempotency_key = idempotency_key.strip()

    if normalized_action not in ALLOWED_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail="action must be approve, resolve, or escalate",
        )
    if not normalized_idempotency_key:
        raise HTTPException(status_code=400, detail="idempotency_key is required")
    if len(normalized_note) > 2000:
        raise HTTPException(status_code=400, detail="note must be at most 2000 characters")

    new_status = "MANUAL_REVIEW" if normalized_action == "escalate" else "CLOSED"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with database() as connection:
        case_exists = connection.execute(
            "SELECT 1 FROM gold_exception_cases WHERE case_id = ?",
            [case_id],
        ).fetchone()

        if case_exists is None:
            raise HTTPException(status_code=404, detail="Case not found")

        connection.execute("BEGIN TRANSACTION")
        try:
            duplicate = connection.execute(
                """
                SELECT 1
                FROM ops_case_actions
                WHERE idempotency_key = ?
                """,
                [normalized_idempotency_key],
            ).fetchone()

            if duplicate is None:
                # The audit event is written first. State is changed only after
                # the immutable action record has been inserted successfully.
                connection.execute(
                    """
                    INSERT INTO ops_case_actions (
                        action_id,
                        case_id,
                        action,
                        note,
                        actor,
                        idempotency_key,
                        action_ts
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(uuid.uuid4()),
                        case_id,
                        normalized_action,
                        normalized_note,
                        LOCAL_ACTOR,
                        normalized_idempotency_key,
                        now,
                    ],
                )

                connection.execute(
                    """
                    INSERT INTO ops_case_state (
                        case_id,
                        status,
                        last_action,
                        last_actor,
                        updated_ts
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (case_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        last_action = EXCLUDED.last_action,
                        last_actor = EXCLUDED.last_actor,
                        updated_ts = EXCLUDED.updated_ts
                    """,
                    [
                        case_id,
                        new_status,
                        normalized_action,
                        LOCAL_ACTOR,
                        now,
                    ],
                )

            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    return RedirectResponse(
        url=f"/cases/{case_id}",
        status_code=303,
    )


@app.get("/kpis")
def kpis_json() -> dict[str, Any]:
    with database() as connection:
        return get_kpis(connection)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
