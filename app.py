from __future__ import annotations
import streamlit as st
import plotly.graph_objects as go
from data import load_data
from simulation import build_curve, build_bottleneck_report

st.set_page_config(page_title="Merge Stage Simulator", layout="wide")
st.title("Merge Stage Simulator — LakeCottage")

EXCEL_PATH = "data/Discovery Event Layout Generator.xlsx"


@st.cache_resource
def get_data():
    return load_data(EXCEL_PATH)


data = get_data()

# ── Sidebar ────────────────────────────────────────────────────────────────
st.sidebar.header("Simulation Parameters")

n_sims = st.sidebar.slider("Simulations (higher = slower but smoother)", 200, 5000, 1000, 200)

st.sidebar.subheader("HarvestAway (Discovery Chain)")
harvest_away_harvests = st.sidebar.number_input(
    "Max harvests per HarvestAway item", min_value=1, max_value=50, value=20
)

st.sidebar.subheader("Puzzle Zone — Required Merge %")
required_pcts: dict[int, float] = {}
for zone_id in [2, 4, 5, 7, 9]:
    required_pcts[zone_id] = (
        st.sidebar.slider(f"Zone {zone_id}", 0, 100, 50, 5) / 100
    )

# ── Zone pair selector ─────────────────────────────────────────────────────
PAIRS = [(3, 4), (3, 5), (6, 7), (8, 9)]
PAIR_LABELS = [f"Zone {g} (Grindy) → Zone {p} (Puzzle)" for g, p in PAIRS]

selected = st.selectbox("Zone Pair to Simulate", PAIR_LABELS)
grindy_id, puzzle_id = PAIRS[PAIR_LABELS.index(selected)]

# ── Run simulation ─────────────────────────────────────────────────────────
with st.spinner(f"Running {n_sims:,} simulations…"):
    curve = build_curve(
        grindy_zone_id=grindy_id,
        puzzle_zone_id=puzzle_id,
        required_merge_pct=required_pcts[puzzle_id],
        harvest_away_max_harvests=int(harvest_away_harvests),
        n_simulations=n_sims,
        data=data,
    )

# ── Chart ─────────────────────────────────────────────────────────────────
x = list(curve.keys())
y = [v * 100 for v in curve.values()]

fig = go.Figure()
fig.add_trace(go.Scatter(
    x=x, y=y,
    mode="lines+markers",
    name="Success probability",
    line=dict(color="#4f8ef7", width=2.5),
    marker=dict(size=7),
))

for ref_val, color, label in [
    (5,  "#e74c3c", "5%  target"),
    (50, "#e67e22", "50% target"),
    (90, "#27ae60", "90% target"),
]:
    fig.add_hline(
        y=ref_val, line_dash="dash", line_color=color,
        annotation_text=label, annotation_position="top right",
    )

fig.update_layout(
    xaxis_title="% of Grindy Zone Harvested",
    yaxis_title="Probability of Completing Puzzle Zone (%)",
    yaxis=dict(range=[0, 105], ticksuffix="%"),
    xaxis=dict(range=[0, 100], ticksuffix="%"),
    height=500,
    margin=dict(r=120),
)
st.plotly_chart(fig, use_container_width=True)

# ── Key thresholds table ───────────────────────────────────────────────────
st.subheader("Key Thresholds")
cols = st.columns(5)
for col, pct in zip(cols, [10, 25, 40, 60, 80]):
    col.metric(f"{pct}% Grinding", f"{curve.get(pct, 0) * 100:.1f}%")

# ── Bottleneck analysis ───────────────────────────────────────────────────
st.divider()
st.subheader("Bottleneck Analysis")

bt_pct = st.slider("Analyze at grinding %", 0, 100, 40, 5)

with st.spinner("Analyzing bottlenecks…"):
    report = build_bottleneck_report(
        grindy_zone_id=grindy_id,
        puzzle_zone_id=puzzle_id,
        required_merge_pct=required_pcts[puzzle_id],
        harvest_away_max_harvests=int(harvest_away_harvests),
        n_simulations=min(n_sims, 500),
        grinding_pct=bt_pct / 100,
        data=data,
    )

c1, c2 = st.columns(2)
c1.metric("🔒 Zone unlock failures", f"{report.unlock_fail_rate * 100:.1f}%",
          help="% of simulations where player couldn't unlock the zone")
c2.metric("🧩 Puzzle completion failures", f"{report.puzzle_fail_rate * 100:.1f}%",
          help="% of simulations where player lacked items to merge")

if report.missing_item_rates:
    st.markdown("**Items blocking puzzle completion** (fraction of simulations where each item was missing):")
    sorted_missing = sorted(report.missing_item_rates.items(), key=lambda x: -x[1])
    fig2 = go.Figure(go.Bar(
        x=[f"{pct * 100:.0f}%" for _, pct in sorted_missing],
        y=[item.split("Competition_")[-1] if "Competition_" in item else item
           for item, _ in sorted_missing],
        orientation="h",
        marker_color="#e74c3c",
    ))
    fig2.update_layout(
        height=max(200, len(sorted_missing) * 30 + 80),
        margin=dict(l=200, r=20, t=20, b=40),
        xaxis_title="Fraction of simulations blocked",
    )
    st.plotly_chart(fig2, use_container_width=True)
else:
    st.success("No puzzle completion bottlenecks at this grinding level.")
