from __future__ import annotations
import hashlib
import io
import streamlit as st
import plotly.graph_objects as go
import numpy as np
import pandas as pd
from data import load_data
from simulation import build_full_run_results, FullRunResult, get_harvest_away_max_harvests, _chain_display_name

st.set_page_config(page_title="Merge Stage Simulator", layout="wide")
st.title("Merge Stage Simulator — LakeCottage")

DEFAULT_LAYOUT_PATH  = "data/Discovery Event Layout Generator 001.xlsx"
DEFAULT_LOOT_PATH    = "data/_Data_Loot - Event LakeCottage.xlsx"
DEFAULT_OBJECTS_PATH = "data/_Data_Objects - Event LakeCottage.xlsx"

# ── File upload section ────────────────────────────────────────────────────────
with st.expander("📂 Data Files", expanded=True):
    st.markdown(
        "Upload Excel files to run the simulation. "
        "Leave empty to use the built-in default files."
    )
    col_l, col_lo, col_o = st.columns(3)
    uploaded_layout = col_l.file_uploader(
        "Layout Generator (.xlsx)", type=["xlsx"], key="layout_file",
        help="Discovery Event Layout Generator file",
    )
    uploaded_loot = col_lo.file_uploader(
        "Loot Data (.xlsx)", type=["xlsx"], key="loot_file",
        help="_Data_Loot - Event LakeCottage file",
    )
    uploaded_objects = col_o.file_uploader(
        "Objects Data (.xlsx)", type=["xlsx"], key="objects_file",
        help="_Data_Objects - Event LakeCottage file",
    )


def _read_bytes(f) -> bytes | None:
    if f is None:
        return None
    f.seek(0)
    return f.read()


@st.cache_resource
def get_data(layout_hash: str, loot_hash: str, objects_hash: str,
             layout_bytes, loot_bytes, objects_bytes):
    layout_src   = io.BytesIO(layout_bytes)   if layout_bytes   else DEFAULT_LAYOUT_PATH
    loot_src     = io.BytesIO(loot_bytes)     if loot_bytes     else DEFAULT_LOOT_PATH
    objects_src  = io.BytesIO(objects_bytes)  if objects_bytes  else DEFAULT_OBJECTS_PATH
    return load_data(layout_src, loot_src, objects_src)


layout_bytes  = _read_bytes(uploaded_layout)
loot_bytes    = _read_bytes(uploaded_loot)
objects_bytes = _read_bytes(uploaded_objects)

layout_hash  = hashlib.md5(layout_bytes).hexdigest()  if layout_bytes  else "default_001"
loot_hash    = hashlib.md5(loot_bytes).hexdigest()    if loot_bytes    else "default_loot"
objects_hash = hashlib.md5(objects_bytes).hexdigest() if objects_bytes else "default_objects"

data = get_data(layout_hash, loot_hash, objects_hash, layout_bytes, loot_bytes, objects_bytes)

active_files = [f"Layout: {'uploaded' if layout_bytes else 'default (001)'}"]
active_files.append(f"Loot: {'uploaded' if loot_bytes else 'default'}")
active_files.append(f"Objects: {'uploaded' if objects_bytes else 'default'}")
st.caption(" · ".join(active_files))

# ── Sidebar ────────────────────────────────────────────────────────────────────
st.sidebar.header("Simulation Parameters")

n_players = st.sidebar.slider("Players", 500, 10_000, 2_000, 500)

puzzle_completion_pct = st.sidebar.slider(
    "Required Puzzle Completion %",
    min_value=0, max_value=100, value=50, step=5,
    help="Player grinds until they can complete this % of each puzzle zone before advancing",
) / 100

harvest_away_max_harvests = get_harvest_away_max_harvests(data)
st.sidebar.caption(f"HarvestAway max harvests: {harvest_away_max_harvests} (from data)")

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
        row: dict = {"Percentile": f"p{p}"}
        for z in zone_ids:
            arr = np.array(value_map[z])
            row[zone_col_labels[z]] = fmt.format(np.percentile(arr, p))
        rows.append(row)
    return pd.DataFrame(rows)


# ── Score percentiles per zone ─────────────────────────────────────────────────
st.subheader("Score Percentiles per Zone")
st.dataframe(_pct_table(result.zone_scores), hide_index=True, use_container_width=True)

# ── Grinding per Grindy Zone ──────────────────────────────────────────────────
st.divider()
st.subheader("Grinding Needed per Grindy Zone")

grindy_zone_ids = sorted(result.zone_grind_pcts.keys())
if grindy_zone_ids:
    fig_grind = go.Figure()
    for zone_id in grindy_zone_ids:
        pcts = np.array(result.zone_grind_pcts[zone_id]) * 100
        if len(pcts) == 0:
            continue
        fig_grind.add_trace(go.Box(
            y=pcts,
            name=f"Zone {zone_id}",
            boxmean=True,
            marker_color="#e67e22",
            line_color="#c0392b",
        ))
    fig_grind.update_layout(
        yaxis=dict(range=[0, 105], ticksuffix="%", title="Grinding % Needed"),
        xaxis_title="Grindy Zone",
        height=380,
        margin=dict(r=40),
        showlegend=False,
    )
    st.plotly_chart(fig_grind, use_container_width=True)
    st.caption(
        "Box shows p25–p75. Whiskers = p5–p95. Dot = mean. "
        "Wide spread = zone is RNG-sensitive."
    )
else:
    st.info("No grindy zones with grinding data.")

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
        st.dataframe(pd.DataFrame(b_rows), hide_index=True, use_container_width=True)
    else:
        st.info("No chain blockers recorded.")
else:
    st.info("No chain blockers recorded.")

# ── Extra Grind for Puzzle Zones ───────────────────────────────────────────────
st.divider()
st.subheader("Extra Grind for Puzzle Zones")
st.caption(
    "Additional grinding % players needed (beyond just getting the discovery item) "
    "in order to satisfy the puzzle completion requirement."
)

puzzle_zone_ids_with_extra = sorted(result.zone_puzzle_extra_grind.keys())
if puzzle_zone_ids_with_extra:
    fig_extra = go.Figure()
    for zone_id in puzzle_zone_ids_with_extra:
        extras = np.array(result.zone_puzzle_extra_grind[zone_id]) * 100
        if len(extras) == 0:
            continue
        fig_extra.add_trace(go.Box(
            y=extras,
            name=f"Zone {zone_id}",
            boxmean=True,
            marker_color="#8e44ad",
            line_color="#6c3483",
        ))
    fig_extra.update_layout(
        yaxis=dict(range=[0, 105], ticksuffix="%", title="Extra Grinding % for Puzzle"),
        xaxis_title="Puzzle Zone",
        height=380,
        margin=dict(r=40),
        showlegend=False,
    )
    st.plotly_chart(fig_extra, use_container_width=True)
    st.caption("0% = puzzle already satisfied at discovery threshold. High values = puzzle completion was the bottleneck.")
else:
    st.info("No puzzle zone extra grind data.")

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
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
else:
    st.info("No chain source data available.")

# ── Healing Power percentiles per zone ────────────────────────────────────────
st.divider()
st.subheader("Healing Power Percentiles per Zone")
st.caption("Cumulative Life Orb healing power at each zone completion.")
st.dataframe(_pct_table(result.zone_healing_power), hide_index=True, use_container_width=True)
