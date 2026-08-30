import os
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st


DUCKDB_PATH = os.getenv("DUCKDB_PATH", "./ops_console.duckdb")

CASE_COLUMNS = [
    "case_id",
    "business_date",
    "channel",
    "case_type",
    "internal_amount",
    "network_amount",
    "amount_diff",
    "internal_status",
    "network_status",
    "disposition",
    "reason",
    "status",
    "first_seen_ts",
    "last_updated_ts",
]


st.set_page_config(
    page_title="Payment Exception Dashboard",
    page_icon="💳",
    layout="wide",
)


@st.cache_data(ttl=30, show_spinner=False)
def table_exists(database_path: str) -> bool:
    """Return whether the exception read-model table exists."""
    with duckdb.connect(database_path, read_only=True) as connection:
        result = connection.execute(
            """
            SELECT COUNT(*) > 0 AS table_exists
            FROM information_schema.tables
            WHERE table_schema = 'main'
              AND table_name = 'gold_exception_cases'
            """
        ).df()

    return bool(result.iloc[0]["table_exists"])


@st.cache_data(ttl=30, show_spinner=False)
def load_cases(database_path: str) -> pd.DataFrame:
    """Load the offline exception read model from DuckDB."""
    with duckdb.connect(database_path, read_only=True) as connection:
        return connection.execute(
            """
            SELECT
                case_id,
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
            FROM gold_exception_cases
            ORDER BY business_date DESC, first_seen_ts ASC, case_id
            """
        ).df()


@st.cache_data(ttl=30, show_spinner=False)
def load_kpis(database_path: str) -> pd.DataFrame:
    """Load dashboard KPIs directly from DuckDB."""
    with duckdb.connect(database_path, read_only=True) as connection:
        return connection.execute(
            """
            SELECT
                COUNT(*)::BIGINT AS total_cases,
                COUNT(*) FILTER (
                    WHERE UPPER(COALESCE(status, '')) = 'OPEN'
                )::BIGINT AS open_count,
                COUNT(*) FILTER (
                    WHERE UPPER(COALESCE(status, '')) = 'AUTO_RESOLVED'
                )::BIGINT AS auto_resolved_count,
                COUNT(*) FILTER (
                    WHERE UPPER(COALESCE(disposition, '')) = 'MANUAL'
                      AND UPPER(COALESCE(status, 'OPEN'))
                          IN ('OPEN', 'MANUAL_REVIEW')
                )::BIGINT AS manual_pending_count,
                COALESCE(SUM(ABS(amount_diff)), 0.0)::DOUBLE
                    AS total_abs_amount_diff
            FROM gold_exception_cases
            """
        ).df()


@st.cache_data(ttl=30, show_spinner=False)
def load_case_type_summary(database_path: str) -> pd.DataFrame:
    """Return case counts grouped by exception type."""
    with duckdb.connect(database_path, read_only=True) as connection:
        return connection.execute(
            """
            SELECT
                case_type,
                COUNT(*)::BIGINT AS case_count
            FROM gold_exception_cases
            GROUP BY case_type
            ORDER BY case_count DESC, case_type
            """
        ).df()


@st.cache_data(ttl=30, show_spinner=False)
def load_channel_exposure(database_path: str) -> pd.DataFrame:
    """Return absolute amount difference grouped by payment channel."""
    with duckdb.connect(database_path, read_only=True) as connection:
        return connection.execute(
            """
            SELECT
                channel,
                COALESCE(SUM(ABS(amount_diff)), 0.0)::DOUBLE
                    AS absolute_amount_difference
            FROM gold_exception_cases
            GROUP BY channel
            ORDER BY absolute_amount_difference DESC, channel
            """
        ).df()


@st.cache_data(ttl=30, show_spinner=False)
def load_daily_trend(database_path: str) -> pd.DataFrame:
    """Return exception volume by business date."""
    with duckdb.connect(database_path, read_only=True) as connection:
        return connection.execute(
            """
            SELECT
                business_date,
                COUNT(*)::BIGINT AS case_count
            FROM gold_exception_cases
            GROUP BY business_date
            ORDER BY business_date
            """
        ).df()


@st.cache_data(ttl=30, show_spinner=False)
def load_disposition_mix(database_path: str) -> pd.DataFrame:
    """Return case counts by AUTO/MANUAL disposition."""
    with duckdb.connect(database_path, read_only=True) as connection:
        return connection.execute(
            """
            SELECT
                UPPER(COALESCE(disposition, 'UNSPECIFIED')) AS disposition,
                COUNT(*)::BIGINT AS case_count
            FROM gold_exception_cases
            GROUP BY UPPER(COALESCE(disposition, 'UNSPECIFIED'))
            ORDER BY disposition
            """
        ).df()


@st.cache_data(ttl=30, show_spinner=False)
def load_status_mix(database_path: str) -> pd.DataFrame:
    """Return case counts by lifecycle status."""
    with duckdb.connect(database_path, read_only=True) as connection:
        return connection.execute(
            """
            SELECT
                UPPER(COALESCE(status, 'UNSPECIFIED')) AS status,
                COUNT(*)::BIGINT AS case_count
            FROM gold_exception_cases
            GROUP BY UPPER(COALESCE(status, 'UNSPECIFIED'))
            ORDER BY status
            """
        ).df()


def show_missing_data_warning() -> None:
    st.warning(
        "No exception data is available. Run the Payment Exception Ops Console "
        "first so it can create and seed the DuckDB database, or set "
        "`DUCKDB_PATH` to an existing DuckDB snapshot containing "
        "`gold_exception_cases`."
    )
    st.stop()


st.title("💳 Payment Exception Dashboard")
st.caption(
    "Offline operational view of payment reconciliation exceptions stored in local DuckDB."
)

database_file = Path(DUCKDB_PATH).expanduser()

if not database_file.exists():
    show_missing_data_warning()

try:
    if not table_exists(str(database_file)):
        show_missing_data_warning()

    cases = load_cases(str(database_file))

    if cases.empty:
        show_missing_data_warning()

    kpis = load_kpis(str(database_file)).iloc[0]
    case_type_summary = load_case_type_summary(str(database_file))
    channel_exposure = load_channel_exposure(str(database_file))
    daily_trend = load_daily_trend(str(database_file))
    disposition_mix = load_disposition_mix(str(database_file))
    status_mix = load_status_mix(str(database_file))
except duckdb.Error as error:
    st.error(f"Unable to read DuckDB at `{database_file}`: {error}")
    show_missing_data_warning()


# Sidebar filters -------------------------------------------------------------

st.sidebar.header("Case filters")
st.sidebar.caption(f"DuckDB: `{database_file}`")

available_channels = sorted(
    value for value in cases["channel"].dropna().unique().tolist()
)
available_case_types = sorted(
    value for value in cases["case_type"].dropna().unique().tolist()
)

selected_channels = st.sidebar.multiselect(
    "Channel",
    options=available_channels,
    default=[],
    placeholder="All channels",
)

selected_case_types = st.sidebar.multiselect(
    "Case type",
    options=available_case_types,
    default=[],
    placeholder="All case types",
)

filtered_cases = cases.copy()

if selected_channels:
    filtered_cases = filtered_cases[
        filtered_cases["channel"].isin(selected_channels)
    ]

if selected_case_types:
    filtered_cases = filtered_cases[
        filtered_cases["case_type"].isin(selected_case_types)
    ]


# KPI row ---------------------------------------------------------------------

kpi_columns = st.columns(5)

kpi_columns[0].metric(
    "Total cases",
    f"{int(kpis['total_cases']):,}",
)
kpi_columns[1].metric(
    "Open",
    f"{int(kpis['open_count']):,}",
)
kpi_columns[2].metric(
    "Auto resolved",
    f"{int(kpis['auto_resolved_count']):,}",
)
kpi_columns[3].metric(
    "Manual pending",
    f"{int(kpis['manual_pending_count']):,}",
)
kpi_columns[4].metric(
    "Total absolute difference",
    f"₹{float(kpis['total_abs_amount_diff']):,.2f}",
)

st.divider()


# Primary charts --------------------------------------------------------------

left_chart, right_chart = st.columns(2)

with left_chart:
    st.subheader("Cases by exception type")
    case_type_chart = (
        case_type_summary.set_index("case_type")[["case_count"]]
        .sort_values("case_count", ascending=False)
    )
    st.bar_chart(
        case_type_chart,
        x_label="Case type",
        y_label="Cases",
        use_container_width=True,
    )

with right_chart:
    st.subheader("Absolute amount difference by channel")
    channel_chart = (
        channel_exposure.set_index("channel")[["absolute_amount_difference"]]
        .sort_values("absolute_amount_difference", ascending=False)
    )
    st.bar_chart(
        channel_chart,
        x_label="Channel",
        y_label="Absolute amount difference",
        use_container_width=True,
    )

st.subheader("Exception aging trend by business date")
daily_trend["business_date"] = pd.to_datetime(daily_trend["business_date"])
daily_chart = (
    daily_trend.sort_values("business_date")
    .set_index("business_date")[["case_count"]]
)
st.area_chart(
    daily_chart,
    x_label="Business date",
    y_label="Cases",
    use_container_width=True,
)


# Disposition and lifecycle mix -----------------------------------------------

disposition_column, lifecycle_column = st.columns(2)

with disposition_column:
    st.subheader("Disposition mix")
    disposition_chart = disposition_mix.set_index("disposition")[["case_count"]]
    st.bar_chart(
        disposition_chart,
        x_label="Disposition",
        y_label="Cases",
        use_container_width=True,
    )

with lifecycle_column:
    st.subheader("Lifecycle status mix")
    lifecycle_chart = status_mix.set_index("status")[["case_count"]]
    st.bar_chart(
        lifecycle_chart,
        x_label="Lifecycle status",
        y_label="Cases",
        use_container_width=True,
    )

st.divider()


# Filtered exception table ----------------------------------------------------

st.subheader("Exception cases")
st.caption(
    f"Showing {len(filtered_cases):,} of {len(cases):,} cases. "
    "Sidebar filters apply to this table."
)

display_columns = [
    "case_id",
    "business_date",
    "channel",
    "case_type",
    "internal_amount",
    "network_amount",
    "amount_diff",
    "internal_status",
    "network_status",
    "disposition",
    "status",
    "first_seen_ts",
    "last_updated_ts",
    "reason",
]

display_cases = filtered_cases[display_columns].copy()
display_cases["business_date"] = pd.to_datetime(
    display_cases["business_date"]
).dt.date
display_cases["first_seen_ts"] = pd.to_datetime(
    display_cases["first_seen_ts"]
)
display_cases["last_updated_ts"] = pd.to_datetime(
    display_cases["last_updated_ts"]
)

st.dataframe(
    display_cases,
    use_container_width=True,
    hide_index=True,
    column_config={
        "case_id": st.column_config.TextColumn("Case ID"),
        "business_date": st.column_config.DateColumn("Business date"),
        "channel": st.column_config.TextColumn("Channel"),
        "case_type": st.column_config.TextColumn("Case type"),
        "internal_amount": st.column_config.NumberColumn(
            "Internal amount",
            format="₹%.2f",
        ),
        "network_amount": st.column_config.NumberColumn(
            "Network amount",
            format="₹%.2f",
        ),
        "amount_diff": st.column_config.NumberColumn(
            "Amount difference",
            format="₹%.2f",
        ),
        "internal_status": st.column_config.TextColumn("Internal status"),
        "network_status": st.column_config.TextColumn("Network status"),
        "disposition": st.column_config.TextColumn("Disposition"),
        "status": st.column_config.TextColumn("Lifecycle status"),
        "first_seen_ts": st.column_config.DatetimeColumn(
            "First seen",
            format="YYYY-MM-DD HH:mm",
        ),
        "last_updated_ts": st.column_config.DatetimeColumn(
            "Last updated",
            format="YYYY-MM-DD HH:mm",
        ),
        "reason": st.column_config.TextColumn("Reason", width="large"),
    },
)
