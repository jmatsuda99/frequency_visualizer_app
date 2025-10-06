# app.py
# -*- coding: utf-8 -*-
"""
周波数の時間変動を可視化する Streamlit アプリ（GitHub + Streamlit Cloud 対応）
- Excel/CSV 読み込み（openpyxl）
- 列マッピング（時間/周波数）
- 統計・±σバンド・ダウンサンプリング
- 中心周波数をパラメータ入力し、偏差（Δf）を別グラフで表示（単位：Hz / mHz 切替）
- ダウンロードは CSV のみ（画像DLなし）
"""
import io
import re
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="周波数可視化", page_icon="📈", layout="wide")
st.title("周波数の時間変動 可視化アプリ")
st.caption("GitHub + Streamlit Cloud で動作 / 日本語対応（画像ダウンロード機能なし）")

TIME_CANDIDATES = [r"time", r"時間", r"時刻", r"秒", r"sec", r"s"]
FREQ_CANDIDATES = [r"freq", r"周波数", r"frequency", r"hz"]

def _find_col(candidates, columns):
    pat = re.compile(r"|".join(candidates), re.IGNORECASE)
    hits = [c for c in columns if pat.search(str(c))]
    return hits[0] if hits else None

@st.cache_data(show_spinner=False)
def read_file(file, sheet_name=None):
    name = getattr(file, "name", "uploaded")
    if name.lower().endswith((".xlsx", ".xls")):
        xls = pd.ExcelFile(file)
        sheets = xls.sheet_names
        if sheet_name is None:
            sheet_name = sheets[0]
        df = xls.parse(sheet_name)
        return df, sheets, sheet_name
    else:
        df = pd.read_csv(file)
        return df, ["(CSV)"], "(CSV)"

def to_numeric_series(s):
    try:
        return pd.to_numeric(s, errors="coerce")
    except Exception:
        return pd.Series(dtype=float)

st.sidebar.header("データ入力")
uploaded = st.sidebar.file_uploader("Excel/CSV をアップロード", type=["xlsx", "xls", "csv"]) 
example_btn = st.sidebar.button("サンプルデータを読み込む")

if example_btn and uploaded is None:
    t = np.arange(1, 1501)
    base = 50 + 0.01*np.sin(2*np.pi*t/300) + 0.005*np.sin(2*np.pi*t/35)
    noise = np.random.normal(0, 0.003, size=t.size)
    df = pd.DataFrame({"time(s)": t, "Frequency(Hz)": base + noise})
    sheets = ["sample"]
    sel_sheet = "sample"
else:
    if uploaded:
        df, sheets, sel_sheet = read_file(uploaded)
    else:
        df, sheets, sel_sheet = None, None, None

if df is None:
    st.info("左のサイドバーからファイルをアップロードするか、『サンプルデータを読み込む』を押してください。")
    st.stop()

if sheets and len(sheets) > 1 and uploaded and uploaded.name.lower().endswith((".xlsx", ".xls")):
    sel_sheet = st.sidebar.selectbox("シート選択", sheets, index=sheets.index(sel_sheet))
    df, _, _ = read_file(uploaded, sheet_name=sel_sheet)

st.sidebar.subheader("列のマッピング")
suggest_time = _find_col(TIME_CANDIDATES, df.columns)
suggest_freq = _find_col(FREQ_CANDIDATES, df.columns)

col_time = st.sidebar.selectbox("時間列", options=[None] + list(df.columns), index=([None]+list(df.columns)).index(suggest_time) if suggest_time in df.columns else 0)
col_freq = st.sidebar.selectbox("周波数列", options=[None] + list(df.columns), index=([None]+list(df.columns)).index(suggest_freq) if suggest_freq in df.columns else 0)

if not col_time or not col_freq:
    st.error("時間列と周波数列を選択してください。")
    st.dataframe(df.head(20))
    st.stop()

x = to_numeric_series(df[col_time]).rename("time")
y = to_numeric_series(df[col_freq]).rename("freq")
mask = ~(x.isna() | y.isna())
df_clean = pd.DataFrame({"time": x[mask], "freq": y[mask]}).reset_index(drop=True)

# ---------- 表示オプション ----------
st.sidebar.header("表示オプション")
resample = st.sidebar.slider("ダウンサンプリング（描画点間隔）", 1, 50, 1, help="大規模データで重い場合に間引きます")
show_sigma = st.sidebar.checkbox("±σバンドを表示（周波数グラフ）", value=True)
unit = st.sidebar.selectbox("時間軸の単位", ["秒 (s)", "相対時刻 (hh:mm:ss)"])

# 中心周波数設定
st.sidebar.header("中心周波数と偏差")
auto_center = st.sidebar.checkbox("平均から自動設定", value=True, help="チェックを外すと手動入力できます")
if auto_center:
    center_freq = float(df_clean["freq"].mean())
else:
    center_freq = st.sidebar.number_input("中心周波数 [Hz]", value=50.0, step=0.001, format="%.3f")

dev_unit = st.sidebar.radio("偏差（Δf）の単位", ["mHz", "Hz"], index=0, horizontal=True)

# ---------- 時間軸整形 ----------
if unit.startswith("相対"):
    t0 = df_clean["time"].iloc[0]
    rel_sec = (df_clean["time"] - t0).to_numpy()
    time_display = pd.to_timedelta(rel_sec, unit="s")
else:
    time_display = df_clean["time"]

plot_df = pd.DataFrame({"time": time_display, "freq": df_clean["freq"]})
plot_df = plot_df.iloc[::resample, :].reset_index(drop=True)

# ---------- 統計 ----------
mean = float(df_clean["freq"].mean())
std = float(df_clean["freq"].std(ddof=0))
min_v = float(df_clean["freq"].min())
max_v = float(df_clean["freq"].max())
count = int(len(df_clean))

# 偏差（Δf）
delta_f_hz = df_clean["freq"] - center_freq
if dev_unit == "mHz":
    delta_display = delta_f_hz * 1000.0
    dev_ylabel = "偏差 Δf [mHz]"
else:
    delta_display = delta_f_hz
    dev_ylabel = "偏差 Δf [Hz]"

dev_stats = {
    "center_freq": center_freq,
    "delta_mean": float(delta_display.mean()),
    "delta_std": float(delta_display.std(ddof=0)),
    "delta_min": float(delta_display.min()),
    "delta_max": float(delta_display.max()),
}

# ---------- 概要 ----------
st.subheader("概要・統計")
colA, colB = st.columns(2)
with colA:
    st.write(
        f"データ点数：**{count}** / 平均：**{mean:.5f} Hz** / 標準偏差：**{std:.5f} Hz** / 最小：**{min_v:.5f} Hz** / 最大：**{max_v:.5f} Hz**"
    )
with colB:
    unit_label = "mHz" if dev_unit == "mHz" else "Hz"
    st.write(
        f"中心周波数：**{center_freq:.5f} Hz** / Δf平均：**{dev_stats['delta_mean']:.3f} {unit_label}** / Δf標準偏差：**{dev_stats['delta_std']:.3f} {unit_label}** / Δf最小：**{dev_stats['delta_min']:.3f} {unit_label}** / Δf最大：**{dev_stats['delta_max']:.3f} {unit_label}**"
    )

# ---------- グラフ：周波数 ----------
fig_f = go.Figure()
fig_f.add_trace(go.Scatter(x=plot_df["time"], y=plot_df["freq"], mode="lines", name="周波数",
                           hovertemplate="時間=%{x}<br>周波数=%{y:.5f} Hz<extra></extra>"))
fig_f.add_hline(y=center_freq, line=dict(width=1, dash="dot"), annotation_text="中心", annotation_position="top left")
if show_sigma:
    fig_f.add_hline(y=mean, line=dict(width=1, dash="dash"), annotation_text="平均", annotation_position="bottom left")
    fig_f.add_hrect(y0=mean-std, y1=mean+std, line_width=0, fillcolor="rgba(0,0,0,0.08)",
                    annotation_text="±σ", annotation_position="top right")

fig_f.update_layout(margin=dict(l=20, r=20, t=40, b=40), title="周波数の時間変動",
                    xaxis_title="時間", yaxis_title="周波数 [Hz]", hovermode="x unified")

# ---------- グラフ：偏差（Δf） ----------
plot_dev = pd.DataFrame({"time": time_display, "delta": delta_display})
plot_dev = plot_dev.iloc[::resample, :].reset_index(drop=True)

fig_d = go.Figure()
fig_d.add_trace(go.Scatter(x=plot_dev["time"], y=plot_dev["delta"], mode="lines", name="偏差 Δf",
                           hovertemplate="時間=%{x}<br>Δf=%{y:.3f}<extra></extra>"))
fig_d.add_hline(y=0.0, line=dict(width=1, dash="dash"), annotation_text="0", annotation_position="top left")
fig_d.update_layout(margin=dict(l=20, r=20, t=40, b=40),
                    title=f"中心 {center_freq:.5f} Hz からの偏差（Δf）",
                    xaxis_title="時間", yaxis_title=dev_ylabel, hovermode="x unified")

# ---------- 描画 ----------
st.plotly_chart(fig_f, use_container_width=True)
st.plotly_chart(fig_d, use_container_width=True)

with st.expander("データ先頭をプレビュー（上位100行）"):
    preview = df_clean.copy()
    preview["delta_f(Hz)"] = (df_clean["freq"] - center_freq)
    st.dataframe(preview.head(100))

# ダウンロード（CSVのみ：Δf列を含める）
csv_buf = io.StringIO()
out_df = df_clean.copy()
out_df["delta_f(Hz)"] = (df_clean["freq"] - center_freq)
out_df.to_csv(csv_buf, index=False)
st.download_button("CSVをダウンロード（Δf含む）", data=csv_buf.getvalue().encode("utf-8"),
                   file_name="frequency_with_delta.csv", mime="text/csv")

st.markdown("---")
st.caption("© 周波数可視化アプリ / 画像のダウンロード機能は提供していません。")
