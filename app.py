"""Streamlit dashboard for the Payment Flow Forensics challenge."""

from __future__ import annotations

import html
import io
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from payment_funnel import (
    apply_filters,
    compute_funnel,
    detect_anomalies,
    load_and_validate_events,
    rank_root_causes,
    reconstruct_sessions,
    segment_funnel,
)

DATA_PATH = Path(__file__).parent / "data" / "transaction_events.csv"
COLORS = {
    "navy": "#0B1835",
    "blue": "#3766E8",
    "teal": "#1DB7A6",
    "amber": "#F4A340",
    "red": "#E45756",
    "muted": "#637083",
    "surface": "#F5F7FB",
}
STAGE_LABELS = {
    "checkout_initiated": "Checkout initiated",
    "method_selected": "Method selected",
    "details_entered": "Details entered",
    "authorization_requested": "Authorization requested",
    "payment_completed": "Payment completed",
}


st.set_page_config(
    page_title="Payment Flow Forensics",
    page_icon="◉",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(
    f"""
    <style>
      .stApp {{ background: {COLORS['surface']}; }}
      [data-testid="stSidebar"] {{ background: #FFFFFF; border-right: 1px solid #E5E9F2; }}
      .block-container {{ padding-top: 2rem; padding-bottom: 3rem; max-width: 1440px; }}
      h1, h2, h3 {{ color: {COLORS['navy']}; letter-spacing: -0.025em; }}
      [data-testid="stMetric"] {{
        background: #FFFFFF; border: 1px solid #E5E9F2; border-radius: 14px;
        padding: 16px 18px; box-shadow: 0 5px 18px rgba(11, 24, 53, 0.04);
      }}
      [data-testid="stMetricValue"] {{ color: {COLORS['navy']}; }}
      .eyebrow {{ color: {COLORS['blue']}; font-weight: 700; font-size: 0.78rem;
        letter-spacing: 0.12em; text-transform: uppercase; margin-bottom: 0.2rem; }}
      .subtitle {{ color: {COLORS['muted']}; max-width: 790px; margin-top: -0.5rem; }}
      .insight-card {{ background: white; border: 1px solid #E5E9F2; border-left: 5px solid {COLORS['blue']};
        border-radius: 12px; padding: 16px 18px; margin-bottom: 12px; }}
      .insight-rank {{ color: {COLORS['blue']}; font-size: 0.75rem; font-weight: 800;
        letter-spacing: 0.08em; text-transform: uppercase; }}
      .insight-title {{ color: {COLORS['navy']}; font-size: 1.04rem; font-weight: 750; margin: 2px 0 6px; }}
      .insight-evidence {{ color: #3D4858; margin-bottom: 6px; }}
      .insight-action {{ color: {COLORS['muted']}; font-size: 0.9rem; }}
      .status-pill {{ display: inline-block; padding: 3px 9px; border-radius: 999px;
        background: #E8F7F4; color: #087F70; font-size: 0.76rem; font-weight: 700; }}
      div[data-testid="stDataFrame"] {{ border: 1px solid #E5E9F2; border-radius: 12px; overflow: hidden; }}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def load_default_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    events = load_and_validate_events(DATA_PATH)
    return events, reconstruct_sessions(events)


@st.cache_data(show_spinner=False)
def load_uploaded_data(contents: bytes) -> tuple[pd.DataFrame, pd.DataFrame]:
    events = load_and_validate_events(io.BytesIO(contents))
    return events, reconstruct_sessions(events)


def base_figure_layout(figure: go.Figure, height: int = 390) -> go.Figure:
    figure.update_layout(
        height=height,
        margin={"l": 20, "r": 20, "t": 55, "b": 20},
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        font={"family": "Inter, Arial, sans-serif", "color": COLORS["navy"]},
        legend_title_text="",
        hoverlabel={"bgcolor": "white", "font_color": COLORS["navy"]},
    )
    return figure


with st.sidebar:
    st.markdown("### Payment Flow Forensics")
    st.caption("HorizonMarket · diagnostic workspace")
    uploaded = st.file_uploader("Load event log", type="csv", help="Use the included dataset or upload the same schema.")

try:
    events, sessions = load_uploaded_data(uploaded.getvalue()) if uploaded else load_default_data()
except ValueError as exc:
    st.error(f"The event log could not be loaded: {exc}")
    st.stop()

with st.sidebar:
    st.markdown("---")
    st.markdown("#### Filters")
    min_date, max_date = sessions["date"].min(), sessions["date"].max()
    selected_dates = st.date_input("Date range", value=(min_date, max_date), min_value=min_date, max_value=max_date)
    filter_specs = {
        "payment_method": "Payment method",
        "country": "Country",
        "currency": "Currency",
        "device_type": "Device",
        "browser": "Browser",
        "processor": "Processor",
    }
    filters: dict[str, object] = {}
    if isinstance(selected_dates, (tuple, list)) and len(selected_dates) == 2:
        filters["date_range"] = (selected_dates[0], selected_dates[1])
    for column, label in filter_specs.items():
        options = sorted(sessions[column].dropna().astype(str).unique())
        filters[column] = st.multiselect(label, options)
    st.markdown("---")
    st.caption("All timestamps are UTC. Empty selections include every value.")

filtered_sessions = apply_filters(sessions, filters)
filtered_ids = set(filtered_sessions["session_id"])
filtered_events = events.loc[events["session_id"].isin(filtered_ids)].copy()

st.markdown('<div class="eyebrow">Payment intelligence / diagnostic report</div>', unsafe_allow_html=True)
st.title("Where the checkout loses customers")
st.markdown(
    '<p class="subtitle">A session-level reconstruction of HorizonMarket’s payment journey, '
    "with the failure patterns and cohorts that deserve action first.</p>",
    unsafe_allow_html=True,
)
st.markdown(
    f'<span class="status-pill">{len(filtered_sessions):,} sessions in current view</span>',
    unsafe_allow_html=True,
)

if filtered_sessions.empty:
    st.warning("No sessions match the selected filters. Broaden the cohort to continue.")
    st.stop()

funnel = compute_funnel(filtered_sessions)
completion_rate = 100 * filtered_sessions["completed"].mean()
incomplete_rate = 100 - completion_rate
largest_drop = funnel.iloc[1:].sort_values("dropoff_from_previous_count", ascending=False).iloc[0]
failed_count = int(filtered_sessions["outcome"].eq("failed").sum())
pending_count = int(filtered_sessions["outcome"].eq("pending").sum())

kpi_columns = st.columns(5)
kpi_columns[0].metric("Checkout sessions", f"{len(filtered_sessions):,}")
kpi_columns[1].metric("Completion rate", f"{completion_rate:.1f}%", f"{int(filtered_sessions['completed'].sum()):,} paid")
kpi_columns[2].metric("Non-completion", f"{incomplete_rate:.1f}%", f"{len(filtered_sessions) - int(filtered_sessions['completed'].sum()):,} sessions", delta_color="inverse")
kpi_columns[3].metric("Failed authorizations", f"{failed_count:,}")
kpi_columns[4].metric("Pending", f"{pending_count:,}")

overview_tab, segments_tab, causes_tab, anomalies_tab, sessions_tab = st.tabs(
    ["Overview", "Segments", "Root causes", "Anomalies", "Session drill-down"]
)

with overview_tab:
    st.markdown("### Funnel health")
    funnel_col, outcome_col = st.columns([1.6, 1])
    with funnel_col:
        funnel_chart = go.Figure(
            go.Funnel(
                y=[STAGE_LABELS[stage] for stage in funnel["stage"]],
                x=funnel["reached_sessions"],
                textinfo="value+percent initial",
                marker={"color": ["#3766E8", "#4C7AF0", "#5E8DF2", "#35A9B3", "#1DB7A6"]},
                connector={"line": {"color": "#D9E0ED", "width": 1}},
            )
        )
        funnel_chart.update_layout(title="Sessions reaching each stage")
        st.plotly_chart(base_figure_layout(funnel_chart, 420), width="stretch")
    with outcome_col:
        outcome_counts = filtered_sessions["outcome"].value_counts().rename_axis("outcome").reset_index(name="sessions")
        outcome_chart = px.pie(
            outcome_counts,
            values="sessions",
            names="outcome",
            hole=0.62,
            color="outcome",
            color_discrete_map={"success": COLORS["teal"], "failed": COLORS["red"], "abandoned": COLORS["amber"], "pending": "#8392AB"},
            title="Final session outcome",
        )
        outcome_chart.update_traces(textposition="outside", textinfo="percent+label")
        st.plotly_chart(base_figure_layout(outcome_chart, 420), width="stretch")

    transition_table = funnel.copy()
    transition_table["stage"] = transition_table["stage"].map(STAGE_LABELS)
    transition_table = transition_table.rename(
        columns={
            "stage": "Stage",
            "reached_sessions": "Reached",
            "conversion_from_previous_pct": "Step conversion %",
            "dropoff_from_previous_count": "Dropped",
            "dropoff_from_previous_pct": "Step drop-off %",
            "overall_conversion_pct": "Overall conversion %",
        }
    )
    st.dataframe(
        transition_table,
        hide_index=True,
        width="stretch",
        column_config={
            "Step conversion %": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
            "Step drop-off %": st.column_config.NumberColumn(format="%.1f%%"),
            "Overall conversion %": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )
    st.caption(
        f"Largest transition loss: {int(largest_drop['dropoff_from_previous_count']):,} sessions "
        f"before {STAGE_LABELS[largest_drop['stage']].lower()} ({largest_drop['dropoff_from_previous_pct']:.1f}%)."
    )

    daily = (
        filtered_sessions.assign(day=pd.to_datetime(filtered_sessions["date"]))
        .groupby("day")
        .agg(sessions=("session_id", "size"), completion_rate=("completed", "mean"))
        .reset_index()
    )
    daily["completion_rate"] *= 100
    trend = px.line(
        daily,
        x="day",
        y="completion_rate",
        markers=True,
        title="Daily checkout completion",
        labels={"day": "Date", "completion_rate": "Completion rate (%)"},
    )
    trend.update_traces(line_color=COLORS["blue"], line_width=3, marker_size=8)
    trend.update_yaxes(range=[0, 100], gridcolor="#E9EDF4")
    trend.update_xaxes(gridcolor="#F1F3F7")
    st.plotly_chart(base_figure_layout(trend, 340), width="stretch")

with segments_tab:
    st.markdown("### Cohort comparison")
    dimension_labels = {
        "payment_method": "Payment method",
        "country": "Country",
        "currency": "Currency",
        "device_type": "Device",
        "browser": "Browser",
        "processor": "Processor",
    }
    dimension = st.selectbox("Break down by", list(dimension_labels), format_func=dimension_labels.get)
    segmented = segment_funnel(filtered_sessions, dimension)
    completion = segmented.loc[segmented["stage"].eq("payment_completed")].copy()
    completion["completion_rate"] = 100 * completion["reached_sessions"] / completion["total_sessions"]
    completion = completion.sort_values("completion_rate")
    segment_chart = px.bar(
        completion,
        x="completion_rate",
        y=dimension,
        orientation="h",
        text="completion_rate",
        color="completion_rate",
        color_continuous_scale=[COLORS["red"], COLORS["amber"], COLORS["teal"]],
        range_color=[0, 100],
        labels={"completion_rate": "Completion rate (%)", dimension: dimension_labels[dimension]},
        title=f"Completion by {dimension_labels[dimension].lower()}",
    )
    segment_chart.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
    segment_chart.update_layout(coloraxis_showscale=False)
    segment_chart.update_xaxes(range=[0, 105], gridcolor="#E9EDF4")
    st.plotly_chart(base_figure_layout(segment_chart, max(340, 62 * len(completion))), width="stretch")

    heatmap_data = pd.crosstab(
        [filtered_sessions["country"]],
        filtered_sessions["payment_method"],
        values=filtered_sessions["completed"].astype(int),
        aggfunc="mean",
    ) * 100
    if not heatmap_data.empty:
        heatmap = px.imshow(
            heatmap_data,
            text_auto=".1f",
            aspect="auto",
            color_continuous_scale=[COLORS["red"], COLORS["amber"], COLORS["teal"]],
            zmin=0,
            zmax=100,
            labels={"x": "Payment method", "y": "Country", "color": "Completion %"},
            title="Country × payment method completion rate",
        )
        st.plotly_chart(base_figure_layout(heatmap, 390), width="stretch")

with causes_tab:
    st.markdown("### Ranked root causes")
    st.caption("Issues are ranked by affected sessions and the size of their deviation from the cohort baseline.")
    insights = rank_root_causes(filtered_events, filtered_sessions, limit=5)
    if insights.empty:
        st.info("This cohort is too small to rank root causes reliably.")
    else:
        for rank, insight in insights.iterrows():
            safe = {
                key: html.escape(str(insight[key]))
                for key in ("category", "issue", "evidence", "recommendation")
            }
            st.markdown(
                f"""
                <div class="insight-card">
                  <div class="insight-rank">Priority {rank + 1} · {safe['category'].replace('_', ' ')}</div>
                  <div class="insight-title">{safe['issue']}</div>
                  <div class="insight-evidence">{safe['evidence']}</div>
                  <div class="insight-action"><strong>Action:</strong> {safe['recommendation']}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    decline_col, processor_col = st.columns(2)
    with decline_col:
        declines = (
            filtered_sessions.loc[
                filtered_sessions["outcome"].eq("failed") & filtered_sessions["decline_code"].ne(""),
                "decline_code",
            ]
            .value_counts()
            .rename_axis("decline_code")
            .reset_index(name="sessions")
        )
        if not declines.empty:
            decline_chart = px.bar(
                declines,
                x="sessions",
                y="decline_code",
                orientation="h",
                title="Authorization decline reasons",
                labels={"sessions": "Failed sessions", "decline_code": "Decline reason"},
                color_discrete_sequence=[COLORS["red"]],
            )
            decline_chart.update_yaxes(categoryorder="total ascending")
            st.plotly_chart(base_figure_layout(decline_chart), width="stretch")
    with processor_col:
        processor = (
            filtered_sessions.groupby("processor")
            .agg(sessions=("session_id", "size"), completion_rate=("completed", "mean"))
            .reset_index()
        )
        processor["completion_rate"] *= 100
        processor_chart = px.bar(
            processor.sort_values("completion_rate"),
            x="completion_rate",
            y="processor",
            orientation="h",
            text="completion_rate",
            title="Processor completion rate",
            labels={"completion_rate": "Completion rate (%)", "processor": "Processor"},
            color_discrete_sequence=[COLORS["blue"]],
        )
        processor_chart.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
        processor_chart.update_xaxes(range=[0, 105])
        st.plotly_chart(base_figure_layout(processor_chart), width="stretch")

with anomalies_tab:
    st.markdown("### Anomaly watchlist")
    anomaly_rows = detect_anomalies(filtered_events, filtered_sessions)
    if anomaly_rows.empty:
        st.success("No anomalies crossed the current detection thresholds.")
    else:
        high_severity = int(anomaly_rows["severity"].eq("high").sum())
        a1, a2, a3 = st.columns(3)
        a1.metric("Anomaly groups", len(anomaly_rows))
        a2.metric("High severity", high_severity)
        a3.metric("Sessions with quality flags", int((filtered_sessions["has_duplicate"] | filtered_sessions["has_out_of_order"]).sum()))
        st.dataframe(
            anomaly_rows.rename(
                columns={
                    "anomaly_type": "Type",
                    "scope": "Scope",
                    "count": "Affected",
                    "rate_pct": "Rate %",
                    "severity": "Severity",
                    "evidence": "Evidence",
                }
            ),
            hide_index=True,
            width="stretch",
            column_config={"Rate %": st.column_config.NumberColumn(format="%.1f%%")},
        )

        flagged = filtered_sessions.loc[
            filtered_sessions["has_duplicate"]
            | filtered_sessions["has_out_of_order"]
            | filtered_sessions["max_gap_seconds"].gt(15 * 60)
            | filtered_sessions["three_ds_seconds"].gt(8)
        ].copy()
        flagged["max_gap_minutes"] = flagged["max_gap_seconds"] / 60
        st.markdown("#### Flagged sessions")
        st.dataframe(
            flagged[
                [
                    "session_id",
                    "payment_method",
                    "processor",
                    "outcome",
                    "max_gap_minutes",
                    "three_ds_seconds",
                    "has_duplicate",
                    "has_out_of_order",
                ]
            ],
            hide_index=True,
            width="stretch",
            column_config={
                "max_gap_minutes": st.column_config.NumberColumn("Max gap (min)", format="%.1f"),
                "three_ds_seconds": st.column_config.NumberColumn("3DS time (sec)", format="%.1f"),
            },
        )

with sessions_tab:
    st.markdown("### Inspect individual checkout attempts")
    c1, c2 = st.columns([1, 2])
    selected_outcomes = c1.multiselect(
        "Outcome",
        sorted(filtered_sessions["outcome"].unique()),
        default=sorted(filtered_sessions["outcome"].unique()),
    )
    search = c2.text_input("Session ID contains", placeholder="e.g. ses_0042")
    drilldown = filtered_sessions.loc[filtered_sessions["outcome"].isin(selected_outcomes)].copy()
    if search:
        drilldown = drilldown.loc[drilldown["session_id"].str.contains(search, case=False, regex=False)]
    display_columns = [
        "session_id",
        "started_at",
        "payment_method",
        "country",
        "processor",
        "device_type",
        "browser",
        "outcome",
        "drop_off_before",
        "decline_code",
        "session_seconds",
    ]
    st.dataframe(drilldown[display_columns], hide_index=True, width="stretch", height=440)
    st.download_button(
        "Download filtered sessions",
        data=drilldown[display_columns].to_csv(index=False).encode("utf-8"),
        file_name="payment_sessions_filtered.csv",
        mime="text/csv",
    )

st.caption(
    f"Source: {'uploaded event log' if uploaded else 'included deterministic demonstration data'} · "
    f"{len(events):,} raw events · generated metrics update with every filter"
)
