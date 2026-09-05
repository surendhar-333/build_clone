import os
from pathlib import Path
from datetime import datetime, timedelta

import duckdb
import pandas as pd
import plotly.express as px
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


def get_dummy_data() -> pd.DataFrame:
    """Generate fallback dummy data if DuckDB is missing or empty."""
    today = datetime.now().date()
    data = []

    # Generate some dummy records
    for i in range(1, 101):
        status = "OPEN" if i % 3 == 0 else ("AUTO_RESOLVED" if i % 2 == 0 else "MANUAL_REVIEW")
        disposition = (
            "AUTO"
            if status == "AUTO_RESOLVED"
            else ("MANUAL" if status == "MANUAL_REVIEW" else "UNSPECIFIED")
        )
        channel = "WEB" if i % 4 == 0 else ("MOBILE" if i % 3 == 0 else "POS")
        case_type = "AMOUNT_MISMATCH" if i % 5 == 0 else "STATUS_MISMATCH"

        data.append(
            {
                "case_id": f"CASE-{i:04d}",
                "business_date": today - timedelta(days=i % 10),
                "channel": channel,
                "case_type": case_type,
                "internal_amount": 100.0 + i,
                "network_amount": 100.0 + i + (i % 5),
                "amount_diff": float(i % 5),
                "internal_status": "SUCCESS",
                "network_status": "FAILED",
                "disposition": disposition,
                "reason": "Test reason",
                "status": status,
                "first_seen_ts": pd.Timestamp.now() - pd.Timedelta(days=i % 10),
                "last_updated_ts": pd.Timestamp.now(),
            }
        )

    return pd.DataFrame(data)[CASE_COLUMNS]


@st.cache_data(ttl=30, show_spinner=False)
def load_cases(database_path: str) -> pd.DataFrame:
    """Load the offline exception read model from DuckDB, with fallback to dummy data."""
    db_path = Path(database_path).expanduser()

    if not db_path.exists():
        st.warning(f"DuckDB database not found at `{db_path}`. Using dummy data.")
        return get_dummy_data()

    try:
        with duckdb.connect(str(db_path), read_only=True) as connection:
            # Check if table exists
            result = connection.execute(
                """
                SELECT COUNT(*) > 0 AS table_exists
                FROM information_schema.tables
                WHERE table_schema = 'main'
                  AND table_name = 'gold_exception_cases'
                """
            ).df()

            if not bool(result.iloc[0]["table_exists"]):
                st.warning("Table `gold_exception_cases` not found in DuckDB. Using dummy data.")
                return get_dummy_data()

            cases = connection.execute(
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

            if cases.empty:
                st.warning("No data found in DuckDB table. Using dummy data.")
                return get_dummy_data()

            return cases

    except duckdb.Error as error:
        st.error(f"Unable to read DuckDB at `{db_path}`: {error}. Using dummy data.")
        return get_dummy_data()


st.title("💳 Payment Exception Dashboard")
st.caption("Offline operational view of payment reconciliation exceptions stored in local DuckDB.")

database_file = Path(DUCKDB_PATH).expanduser()

cases = load_cases(str(database_file))


# Sidebar filters -------------------------------------------------------------

st.sidebar.header("Case filters")
st.sidebar.caption(f"DuckDB: `{database_file}`")

available_channels = sorted(value for value in cases["channel"].dropna().unique().tolist())
available_case_types = sorted(value for value in cases["case_type"].dropna().unique().tolist())

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
    filtered_cases = filtered_cases[filtered_cases["channel"].isin(selected_channels)]

if selected_case_types:
    filtered_cases = filtered_cases[filtered_cases["case_type"].isin(selected_case_types)]

# Calculate KPIs from filtered_cases
total_cases = len(filtered_cases)
open_cases = len(filtered_cases[filtered_cases["status"].str.upper() == "OPEN"])
auto_resolved_cases = len(filtered_cases[filtered_cases["status"].str.upper() == "AUTO_RESOLVED"])
match_rate = (auto_resolved_cases / total_cases * 100) if total_cases > 0 else 0.0
total_exposure = filtered_cases["amount_diff"].abs().sum()

# KPI row ---------------------------------------------------------------------

kpi_columns = st.columns(4)

kpi_columns[0].metric(
    "Total Cases",
    f"{total_cases:,}",
)
kpi_columns[1].metric(
    "Open Cases",
    f"{open_cases:,}",
)
kpi_columns[2].metric(
    "Match Rate",
    f"{match_rate:.1f}%",
)
kpi_columns[3].metric(
    "Total Exposure",
    f"₹{total_exposure:,.2f}",
)

st.divider()


# Primary charts --------------------------------------------------------------

left_chart, right_chart = st.columns(2)

with left_chart:
    st.subheader("Cases by exception type")
    if not filtered_cases.empty:
        case_type_counts = filtered_cases["case_type"].value_counts().reset_index()
        case_type_counts.columns = ["Case type", "Cases"]
        fig_case_type = px.bar(case_type_counts, x="Case type", y="Cases")
        st.plotly_chart(fig_case_type, use_container_width=True)
    else:
        st.info("No data available for this chart.")

with right_chart:
    st.subheader("Absolute amount difference by channel")
    if not filtered_cases.empty:
        channel_exposure = (
            filtered_cases.groupby("channel")["amount_diff"]
            .apply(lambda x: x.abs().sum())
            .reset_index()
        )
        channel_exposure.columns = ["Channel", "Absolute amount difference"]
        channel_exposure = channel_exposure.sort_values(
            "Absolute amount difference", ascending=False
        )
        fig_channel = px.bar(channel_exposure, x="Channel", y="Absolute amount difference")
        st.plotly_chart(fig_channel, use_container_width=True)
    else:
        st.info("No data available for this chart.")

st.subheader("Exception aging trend by business date")
if not filtered_cases.empty:
    daily_trend = (
        filtered_cases.groupby(pd.to_datetime(filtered_cases["business_date"]).dt.date)
        .size()
        .reset_index(name="Cases")
    )
    daily_trend.columns = ["Business date", "Cases"]
    daily_trend = daily_trend.sort_values("Business date")
    fig_daily = px.area(daily_trend, x="Business date", y="Cases")
    st.plotly_chart(fig_daily, use_container_width=True)
else:
    st.info("No data available for this chart.")

# Disposition and lifecycle mix -----------------------------------------------

disposition_column, lifecycle_column = st.columns(2)

with disposition_column:
    st.subheader("Disposition mix")
    if not filtered_cases.empty:
        disposition_counts = (
            filtered_cases["disposition"]
            .fillna("UNSPECIFIED")
            .str.upper()
            .value_counts()
            .reset_index()
        )
        disposition_counts.columns = ["Disposition", "Cases"]
        fig_disp = px.bar(disposition_counts, x="Disposition", y="Cases")
        st.plotly_chart(fig_disp, use_container_width=True)
    else:
        st.info("No data available for this chart.")

with lifecycle_column:
    st.subheader("Lifecycle status mix")
    if not filtered_cases.empty:
        status_counts = (
            filtered_cases["status"].fillna("UNSPECIFIED").str.upper().value_counts().reset_index()
        )
        status_counts.columns = ["Lifecycle status", "Cases"]
        fig_status = px.bar(status_counts, x="Lifecycle status", y="Cases")
        st.plotly_chart(fig_status, use_container_width=True)
    else:
        st.info("No data available for this chart.")

st.divider()


# Filtered exception table ----------------------------------------------------

st.subheader("Exception cases")
st.caption(
    f"Showing {len(filtered_cases):,} of {len(cases):,} cases. Sidebar filters apply to this table."
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

if not filtered_cases.empty:
    display_cases = filtered_cases[display_columns].copy()
    display_cases["business_date"] = pd.to_datetime(display_cases["business_date"]).dt.date
    display_cases["first_seen_ts"] = pd.to_datetime(display_cases["first_seen_ts"])
    display_cases["last_updated_ts"] = pd.to_datetime(display_cases["last_updated_ts"])
else:
    display_cases = pd.DataFrame(columns=display_columns)

st.dataframe(
    display_cases,
    width="stretch",
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
