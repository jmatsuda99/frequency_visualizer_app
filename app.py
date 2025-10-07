# app.py
# -*- coding: utf-8 -*-
"""
周波数偏差（Δf）の時系列 & ヒストグラム（±1σ/2σ/3σ線入り）
- Excel/CSV読み込み（openpyxl対応）
- 中心周波数はデータ平均を既定（手動指定も可）
- Δf時系列グラフ：0基準に±1σ/2σ/3σの基準線
- ヒストグラム：±1σ/2σ/3σの縦線表示、中心線(0)も表示
"""
import io
import re
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="周波数偏差（Δf）±σ 可視化", page_icon="📈", layout="wide")
st.title("周波数偏差（Δf）の可視化：時系列 & ヒストグラム（±σ）")

TIME_CANDS = [r"time", r"時間", r"時刻", r"秒", r"sec", r"s", r"min", r"hour"]
FREQ_CANDS = [r"freq", r"周波数", r"frequency", r"hz"]

def _find_col(cands, cols):
    pat = re.compile(r"|".join(cands), re.IGNORECASE)
    hits = [c for c in cols if pat.search(str(c))]
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

def to_num(s):
    try:
        return pd.to_numeric(s, errors="coerce")
    except Exception:
        return pd.Series(dtype=float)

# ---------------- 入力 ----------------
st.sidebar.header("データ入力")
uploaded = st.sidebar.file_uploader("Excel/CSV をアップロード", type=["xlsx","xls","csv"])
example_btn = st.sidebar.button("サンプルデータを読み込む")

if example_btn and uploaded is None:
    t = np.arange(0, 3600, 1)  # 1時間、1秒間隔
    base = 50 + 0.01*np.sin(2*np.pi*t/300) + 0.005*np.sin(2*np.pi*t/35)
    noise = np.random.normal(0, 0.003, size=t.size)
    df = pd.DataFrame({"time(s)": t, "Frequency(Hz)": base + noise})
    sheets = ["sample"]; sel_sheet = "sample"
else:
    if uploaded:
        df, sheets, sel_sheet = read_file(uploaded)
    else:
        df = sheets = sel_sheet = None

if df is None:
    st.info("左のサイドバーからファイルをアップロードするか、『サンプルデータを読み込む』を押してください。")
    st.stop()

# シート選択（Excelのみ）
if sheets and len(sheets)>1 and uploaded and uploaded.name.lower().endswith((".xlsx",".xls")):
    sel_sheet = st.sidebar.selectbox("シート選択", sheets, index=sheets.index(sel_sheet))
    df, _, _ = read_file(uploaded, sheet_name=sel_sheet)

st.sidebar.subheader("列のマッピング")
tc = _find_col(TIME_CANDS, df.columns); fc = _find_col(FREQ_CANDS, df.columns)
col_time = st.sidebar.selectbox("時間列", [None]+list(df.columns), index=([None]+list(df.columns)).index(tc) if tc in df.columns else 0)
col_freq = st.sidebar.selectbox("周波数列", [None]+list(df.columns), index=([None]+list(df.columns)).index(fc) if fc in df.columns else 0)
if not col_time or not col_freq:
    st.error("時間列と周波数列を選択してください。"); st.stop()

x = to_num(df[col_time]); y = to_num(df[col_freq])
mask = ~(x.isna() | y.isna())
dfc = pd.DataFrame({"time": x[mask], "freq": y[mask]}).reset_index(drop=True)

st.sidebar.subheader("中心周波数")
auto_center = st.sidebar.checkbox("平均から自動設定（推奨）", value=True)
if auto_center:
    f_center = float(dfc["freq"].mean())
else:
    f_center = st.sidebar.number_input("中心周波数 [Hz]", value=50.0, step=0.001, format="%.3f")

bin_count = st.sidebar.slider("ヒストグラムのビン数", min_value=20, max_value=200, value=60, step=5)

# Δfとσ
delta_f = (dfc["freq"] - f_center).to_numpy()
sigma = float(np.std(delta_f, ddof=1))  # 標本標準偏差

# --- 時系列（Δf） ---
time0 = dfc["time"].to_numpy(float)
td = pd.to_timedelta(time0 - time0[0], unit="s")  # 経過時間表示

fig_ts = go.Figure()
fig_ts.add_trace(go.Scatter(x=td, y=delta_f, mode="lines", name="Δf [Hz]"))
fig_ts.add_hline(y=0.0, line=dict(dash="dash"), annotation_text=f"中心 = {f_center:.5f} Hz（Δf=0）", annotation_position="top left")

for n in (1,2,3):
    y = n * sigma
    fig_ts.add_hline(y= y, line=dict(dash="dot"), annotation_text=f"+{n}σ = { y:+.5f} Hz", annotation_position="top left")
    fig_ts.add_hline(y=-y, line=dict(dash="dot"), annotation_text=f"-{n}σ = {-y:+.5f} Hz", annotation_position="bottom left")

fig_ts.update_layout(title="周波数偏差（Δf）の時間変動と ±1σ/2σ/3σ", xaxis_title="時間", yaxis_title="Δf [Hz]", hovermode="x unified")

# --- ヒストグラム（Δf） ---
fig_hist = go.Figure()
fig_hist.add_trace(go.Histogram(x=delta_f, nbinsx=bin_count, name="Δf 分布", opacity=0.75, histnorm=""))
# 中心線と±σ線
fig_hist.add_vline(x=0.0, line=dict(dash="dash"), annotation_text="中心 (Δf=0)", annotation_position="top left")
for n in (1,2,3):
    xline = n * sigma
    fig_hist.add_vline(x= xline, line=dict(dash="dot"), annotation_text=f"+{n}σ = { xline:+.5f} Hz", annotation_position="top left")
    fig_hist.add_vline(x=-xline, line=dict(dash="dot"), annotation_text=f"-{n}σ = {-xline:+.5f} Hz", annotation_position="bottom left")

fig_hist.update_layout(title="周波数偏差（Δf）ヒストグラムと ±1σ/2σ/3σ", xaxis_title="Δf [Hz]", yaxis_title="度数", bargap=0.02)

# 描画
st.plotly_chart(fig_ts, use_container_width=True)
st.plotly_chart(fig_hist, use_container_width=True)

# サマリ
st.subheader("統計サマリ")
c1,c2,c3,c4,c5 = st.columns(5)
c1.metric("中心周波数(Hz)", f"{f_center:.5f}")
c2.metric("σ(標本) [Hz]", f"{sigma:.6f}")
c3.metric("+1σ [Hz]", f"{(+1*sigma):+.6f}")
c4.metric("+2σ [Hz]", f"{(+2*sigma):+.6f}")
c5.metric("+3σ [Hz]", f"{(+3*sigma):+.6f}")

# CSV出力（Δfと基本統計）
csv_buf = io.StringIO()
out = pd.DataFrame({"time[s]": time0, "freq[Hz]": dfc["freq"], "delta_f[Hz]": delta_f})
out.to_csv(csv_buf, index=False)
st.download_button("CSVダウンロード（time, freq, Δf）", data=csv_buf.getvalue().encode("utf-8"),
                   file_name="delta_f_with_time.csv", mime="text/csv")
st.caption("σは標本標準偏差（ddof=1）で算出。ヒストグラムのビン数はサイドバーで変更できます。")
