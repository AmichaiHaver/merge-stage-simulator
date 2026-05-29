from __future__ import annotations
import hashlib
import io
import os
import streamlit as st
import plotly.graph_objects as go
import numpy as np
import pandas as pd
from data import load_data
from simulation import build_full_run_results, FullRunResult, get_harvest_away_max_harvests, _chain_display_name

st.set_page_config(page_title="Merge Stage Simulator", layout="wide")
st.title("Merge Stage Simulator — LakeCottage")

DEFAULT_LAYOUT_PATH    = "data/Discovery Event Layout Generator 002.xlsx"
DEFAULT_GE_REVAMP_PATH = "data/GE Revamp Data Editor.xlsx"

# ── File upload section ────────────────────────────────────────────────────────
with st.expander("📂 Data Files", expanded=True):
    st.markdown(
        "Upload Excel files to run the simulation. "
        "Leave empty to use the built-in default files."
    )
    col_l, col_ge = st.columns(2)
    uploaded_layout = col_l.file_uploader(
        "Layout Generator (.xlsx)", type=["xlsx"], key="layout_file",
        help="Discovery Event Layout Generator file",
    )
    uploaded_ge_revamp = col_ge.file_uploader(
        "GE Revamp Data Editor (.xlsx)", type=["xlsx"], key="ge_revamp_file",
        help="GE Revamp Data Editor — harvest charges, seconds, on_die",
    )


_DEFAULTS_EXIST = all(os.path.exists(p) for p in [
    DEFAULT_LAYOUT_PATH, DEFAULT_GE_REVAMP_PATH
])


def _read_bytes(f) -> bytes | None:
    if f is None:
        return None
    f.seek(0)
    return f.read()


@st.cache_resource
def get_data(layout_hash: str, ge_revamp_hash: str, layout_bytes, ge_revamp_bytes):
    layout_src    = io.BytesIO(layout_bytes)    if layout_bytes    else DEFAULT_LAYOUT_PATH
    ge_revamp_src = io.BytesIO(ge_revamp_bytes) if ge_revamp_bytes else DEFAULT_GE_REVAMP_PATH
    return load_data(layout_src, ge_revamp_src)


layout_bytes    = _read_bytes(uploaded_layout)
ge_revamp_bytes = _read_bytes(uploaded_ge_revamp)

if not _DEFAULTS_EXIST:
    missing = []
    if not layout_bytes:    missing.append("Layout Generator")
    if not ge_revamp_bytes: missing.append("GE Revamp Data Editor")
    if missing:
        st.warning(f"Please upload both Excel files to run the simulation.\n\nMissing: {', '.join(missing)}")
        st.stop()

layout_hash    = hashlib.md5(layout_bytes).hexdigest()    if layout_bytes    else "default_002"
ge_revamp_hash = hashlib.md5(ge_revamp_bytes).hexdigest() if ge_revamp_bytes else "default_ge_revamp"

data = get_data(layout_hash, ge_revamp_hash, layout_bytes, ge_revamp_bytes)

active_files = [f"Layout: {'uploaded' if layout_bytes else 'default (002)'}"]
active_files.append(f"GE Revamp: {'uploaded' if ge_revamp_bytes else 'default'}")
st.caption(" · ".join(active_files))

# ── Sidebar ────────────────────────────────────────────────────────────────────
st.sidebar.header("Simulation Parameters")

n_players = st.sidebar.slider("Players", 500, 10_000, 2_000, 500)

puzzle_completion_pct = st.sidebar.slider(
    "Required Puzzle Completion %",
    min_value=0, max_value=100, value=50, step=5,
    help="Player grinds until they can complete this % of each puzzle zone before advancing",
) / 100

n_dragons = st.sidebar.slider(
    "Dragons (parallel harvesters)",
    min_value=1, max_value=5, value=1, step=1,
    help="Number of dragons harvesting in parallel — divides total harvest time per zone",
)

harvest_away_max_harvests = get_harvest_away_max_harvests(data)

# ── Run simulation ─────────────────────────────────────────────────────────────
with st.spinner(f"Simulating {n_players:,} players through the full event…"):
    result: FullRunResult = build_full_run_results(
        data=data,
        puzzle_completion_pct=puzzle_completion_pct,
        harvest_away_max_harvests=harvest_away_max_harvests,
        n_players=n_players,
    )

_PCTS = [5, 10, 25, 50, 75, 90, 95]
non_fog_zone_ids = sorted(z for z in data.zones if data.zones[z].zone_type not in ("Fog",))
zone_col_labels = {z: f"Z{z} ({data.zones[z].zone_type[0]})" for z in non_fog_zone_ids}


def _pct_table(value_map: dict[int, list[float]], fmt: str = "{:,.0f}") -> pd.DataFrame:
    """Build percentile × zone DataFrame. Only zones in value_map appear."""
    zone_ids = [z for z in non_fog_zone_ids if z in value_map and value_map[z]]
    rows = []
    for p in _PCTS:
        row: dict = {}
        for z in zone_ids:
            arr = np.array(value_map[z])
            row[zone_col_labels[z]] = fmt.format(np.percentile(arr, p))
        rows.append(row)
    df = pd.DataFrame(rows, index=[f"p{p}" for p in _PCTS])
    df.index.name = "Percentile"
    return df


# ── Score percentiles per zone ─────────────────────────────────────────────────
st.subheader("Score Percentiles per Zone")
st.dataframe(_pct_table(result.zone_scores), hide_index=False, use_container_width=True)

# ── Max Discovery Item Level per Zone ────────────────────────────────────────
st.divider()
st.subheader("Max Discovery Item Level per Zone (after merging)")
st.caption(
    "Highest-level currency item each player can hold after merging all discovery items in inventory. "
    "Level 1 = base item. Merges: 5→2 preferred, then 3→1 on remainder."
)
_disc_levels: dict[int, list[float]] = {
    z: [float(v) for v in vals]
    for z, vals in result.zone_max_discovery_level.items()
}
st.dataframe(_pct_table(_disc_levels, fmt="{:.0f}"), hide_index=False, use_container_width=True)

# ── Harvest Efficiency per Zone ───────────────────────────────────────────────
st.divider()
st.subheader("Harvest Efficiency per Zone")
st.caption(
    "Cumulative actual harvests / cumulative possible harvests up to each zone. "
    "Grindy zones: harvests done to satisfy discovery + puzzle unlock. "
    "Puzzle/Start zones: 100% of HarvestAway and Bramble tiles. "
    "Only HarvestAway and Bramble charges counted."
)

eff_zone_ids = sorted(z for z in result.zone_harvest_efficiency.keys() if z <= 9)
if eff_zone_ids:
    fig_eff = go.Figure()
    for zone_id in eff_zone_ids:
        effs = np.array(result.zone_harvest_efficiency[zone_id]) * 100
        if len(effs) == 0:
            continue
        zone_type = data.zones[zone_id].zone_type if zone_id in data.zones else "?"
        fig_eff.add_trace(go.Box(
            y=effs,
            name=f"Z{zone_id} ({zone_type[0]})",
            boxmean=True,
            marker_color="#2980b9",
            line_color="#1a5276",
        ))
    fig_eff.update_layout(
        yaxis=dict(range=[0, 105], ticksuffix="%", title="Harvest Efficiency (cumulative)"),
        xaxis_title="Zone",
        height=420,
        margin=dict(r=40),
        showlegend=False,
    )
    st.plotly_chart(fig_eff, use_container_width=True)
    st.caption("Box = p25–p75. Whiskers = p5–p95. Dot = mean. Lower = player harvested small fraction of available tiles.")
    st.dataframe(
        _pct_table(result.zone_harvest_efficiency, fmt="{:.1%}"),
        hide_index=False,
        use_container_width=True,
    )
else:
    st.info("No harvest efficiency data.")

# ── Blocker breakdown per Zone ─────────────────────────────────────────────────
st.divider()
st.subheader("Chain Blockers per Zone")
st.caption(
    "Chains that individually fell below the puzzle completion % threshold when a zone was resolved. "
    "Grindy zones: even 100% grind couldn't satisfy downstream puzzle. "
    "Puzzle zones: chain was under X% even though overall tile count passed."
)

all_zone_ids_with_blockers = sorted(result.zone_chain_blockers.keys())
if all_zone_ids_with_blockers:
    all_chain_names: list[str] = []
    for z in all_zone_ids_with_blockers:
        for name in result.zone_chain_blockers[z]:
            if name not in all_chain_names:
                all_chain_names.append(name)

    b_rows = []
    for z in all_zone_ids_with_blockers:
        counts = result.zone_chain_blockers[z]
        total_blocked = sum(counts.values())
        if total_blocked == 0:
            continue
        zone_type = data.zones[z].zone_type if z in data.zones else "?"
        row: dict = {
            "Zone": f"Z{z} ({zone_type[0]})",
            "Blocked players": total_blocked,
        }
        for name in all_chain_names:
            n = counts.get(name, 0)
            row[name] = f"{100 * n / total_blocked:.0f}%" if total_blocked else "0%"
        b_rows.append(row)

    if b_rows:
        _b_df = pd.DataFrame(b_rows).set_index("Zone")
        st.dataframe(_b_df, hide_index=False, use_container_width=True)
    else:
        st.info("No chain blockers recorded.")
else:
    st.info("No chain blockers recorded.")


# ── Players Stuck per Zone ─────────────────────────────────────────────────────
st.divider()
st.subheader("Players Stuck per Zone")
st.caption(
    "Grindy zones where even 100% grind was insufficient. "
    "These players did not advance to subsequent zones."
)

n_players_total = len(result.scores)
if result.zone_stuck_counts:
    stuck_rows = []
    for z in sorted(result.zone_stuck_counts.keys()):
        n_stuck = result.zone_stuck_counts[z]
        zone_type = data.zones[z].zone_type if z in data.zones else "?"
        stuck_rows.append({
            "Zone": f"Z{z} ({zone_type[0]})",
            "Players Stuck": n_stuck,
            "% of All Players": f"{100 * n_stuck / n_players_total:.1f}%",
        })
    _stuck_df = pd.DataFrame(stuck_rows).set_index("Zone")
    st.dataframe(_stuck_df, hide_index=False, use_container_width=True)
else:
    st.info("No players got stuck (100% grind was always sufficient).")


# ── Chain Source Breakdown per Puzzle Zone ─────────────────────────────────────
st.divider()
st.subheader("Chain Item Sources per Puzzle Zone")
st.caption(
    "Median base units per chain (3^level conversion). "
    "'Bramble' = items from puzzle zone's own harvest. 'Other' = items from prior grindy zones."
)

if result.zone_puzzle_chain_sources:
    for zone_id in sorted(result.zone_puzzle_chain_sources.keys()):
        chain_map = result.zone_puzzle_chain_sources[zone_id]
        if not chain_map:
            continue
        st.markdown(f"**Zone {zone_id}**")
        rows = []
        for chain_key, sources in chain_map.items():
            bramble_vals = np.array(sources.get('bramble', [0]))
            other_vals = np.array(sources.get('other', [0]))
            total_med = float(np.median(bramble_vals + other_vals))
            bramble_med = float(np.median(bramble_vals))
            other_med = float(np.median(other_vals))
            bramble_pct = 100 * bramble_med / total_med if total_med > 0 else 0
            rows.append({
                "Chain": _chain_display_name(chain_key),
                "Bramble (median base units)": f"{bramble_med:,.0f}",
                "Other (median base units)": f"{other_med:,.0f}",
                "Bramble %": f"{bramble_pct:.0f}%",
            })
        if rows:
            _src_df = pd.DataFrame(rows).set_index("Chain")
            st.dataframe(_src_df, hide_index=False, use_container_width=True)
else:
    st.info("No chain source data available.")

# ── Healing Power percentiles per zone ────────────────────────────────────────
st.divider()
st.subheader("Healing Power Percentiles per Zone")
st.caption("Cumulative Life Orb healing power at each zone completion (minimum grind to pass).")
st.dataframe(_pct_table(result.zone_healing_power), hide_index=False, use_container_width=True)

st.subheader("Max Possible Healing Power per Zone")
st.caption("Cumulative healing if player does 100% harvesting in every grindy zone.")
st.dataframe(_pct_table(result.zone_max_healing_power), hide_index=False, use_container_width=True)

# ── Time to complete each zone ─────────────────────────────────────────────────
st.divider()
st.subheader("Time to Complete Each Zone")
st.caption(
    f"Harvest time per zone (players who reached it) with {n_dragons} dragon(s). "
    "Parallel dragons divide harvest time. Does not include travel / merge time."
)

_time_zone_ids = [z for z in non_fog_zone_ids if z in result.zone_harvest_seconds and result.zone_harvest_seconds[z]]
if _time_zone_ids:
    # Per-zone harvest time (players who reached each zone)
    _zone_time: dict[int, np.ndarray] = {}
    for z in _time_zone_ids:
        arr = np.array(result.zone_harvest_seconds[z])
        if len(arr) > 0:
            _zone_time[z] = arr / n_dragons

    def _fmt_time(secs: float) -> str:
        m = int(secs // 60)
        h = m // 60
        return f"{h}h {m % 60}m" if h > 0 else f"{m}m"

    time_table_rows = []
    for p in _PCTS:
        row: dict = {"Percentile": f"p{p}"}
        for z in _time_zone_ids:
            if z in _zone_time:
                row[zone_col_labels[z]] = _fmt_time(float(np.percentile(_zone_time[z], p)))
        time_table_rows.append(row)

    _time_df = pd.DataFrame(time_table_rows).set_index("Percentile")
    st.dataframe(_time_df, hide_index=False, use_container_width=True)
else:
    st.info("No harvest time data. Ensure GE Revamp Data Editor is loaded.")

# ── Item Inventory Distribution per Zone ──────────────────────────────────────
st.divider()
st.subheader("Item Inventory Distribution per Zone")
st.caption(
    "% of players who had ≥X of each item at end of each zone. "
    "Rows: currency chain then puzzle chains (low→high level). Columns: zone × threshold (0–10+)."
)

_inv_zone_ids = [z for z in non_fog_zone_ids if z in result.zone_item_counts]
if _inv_zone_ids and (data.chains or data.currency_chain):
    # Build ordered list of (chain_label, item_prefab, level_label)
    _chain_items: list[tuple[str, str, str]] = []
    for i, item in enumerate(data.currency_chain.items):
        _chain_items.append(("Discovery", item, f"Lvl {i+1}"))
    for chain in data.chains:
        lbl = _chain_display_name(chain.items[0]) if chain.items else chain.name
        for i, item in enumerate(chain.items):
            _chain_items.append((lbl, item, f"Lvl {i+1}"))

    _THRESHOLDS = list(range(11))  # 0..10

    # MultiIndex columns: (zone_label, threshold_label)
    _col_tuples = [
        (zone_col_labels[z], f"≥{x}" if x < 10 else "≥10")
        for z in _inv_zone_ids
        for x in _THRESHOLDS
    ]
    _mi = pd.MultiIndex.from_tuples(_col_tuples)

    _rows = []
    for chain_lbl, prefab, lvl_lbl in _chain_items:
        row_data = []
        for z in _inv_zone_ids:
            counts = result.zone_item_counts[z].get(prefab, [])
            n = len(counts)
            for x in _THRESHOLDS:
                if n == 0:
                    row_data.append(None)
                else:
                    row_data.append(f"{sum(1 for c in counts if c >= x) / n * 100:.0f}%")
        _rows.append([chain_lbl, lvl_lbl] + row_data)

    _df_inv = pd.DataFrame(_rows, columns=pd.MultiIndex.from_tuples(
        [("", "Chain"), ("", "Level")] + _col_tuples
    ))
    _df_inv_indexed = _df_inv.set_index([("", "Chain"), ("", "Level")])
    _df_inv_indexed.index.names = ["Chain", "Level"]
    st.dataframe(_df_inv_indexed, hide_index=False, use_container_width=True)
else:
    st.info("No inventory snapshot data.")
