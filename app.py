# app.py
# -*- coding: utf-8 -*-
"""
周波数の時間変動に連動した蓄電システム（BESS）の動作を可視化する Streamlit アプリ
- Excel/CSV 読み込み（openpyxl）
- 中心周波数（基準周波数）/ 偏差（Δf）算出（Hz / mHz）
- BESS応答：調停率（Droop, %）・不感帯（mHz）・上限/下限出力（%）をパラメータ指定
- 出力は「出力指令[%]」として表示（+は放電、-は充電を想定）
- ダウンロードは CSV のみ（画像DLなし）
"""
import io
import re
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="周波数×BESS応答 可視化", page_icon="🔌", layout="wide")
st.title("周波数変動とBESS応答の可視化")
st.caption("GitHub + Streamlit Cloud で動作 / 画像DLなし")

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

# ---------------- サイドバー：入力 ----------------
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

# シート選択
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

# 整形
x = to_numeric_series(df[col_time]).rename("time")
y = to_numeric_series(df[col_freq]).rename("freq")
mask = ~(x.isna() | y.isna())
df_clean = pd.DataFrame({"time": x[mask], "freq": y[mask]}).reset_index(drop=True)

# ---------------- パラメータ ----------------
st.sidebar.header("表示オプション")
resample = st.sidebar.slider("ダウンサンプリング（描画点間隔）", 1, 50, 1)

unit_time = st.sidebar.selectbox("時間軸の単位", ["秒 (s)", "相対時刻 (hh:mm:ss)"])

st.sidebar.header("中心周波数・Δf")
auto_center = st.sidebar.checkbox("平均から自動設定", value=True)
if auto_center:
    f_center = float(df_clean["freq"].mean())
else:
    f_center = st.sidebar.number_input("中心周波数 [Hz]", value=50.0, step=0.001, format="%.3f")
dev_unit = st.sidebar.radio("偏差（Δf）の単位", ["mHz", "Hz"], index=0, horizontal=True)

st.sidebar.header("BESS 応答（Droop制御）")
f_nom = st.sidebar.number_input("系統公称周波数 [Hz]", value=50.0, step=0.1, format="%.1f")
droop_pct = st.sidebar.number_input("調停率 Droop [%]", value=5.0, min_value=0.1, step=0.1, help="5% は周波数が5%変化（2.5Hz@50Hz）で100%出力変化を意味")
deadband_mhz = st.sidebar.number_input("不感帯 [mHz]", value=0.0, min_value=0.0, step=1.0)
limit_pos = st.sidebar.number_input("上限出力（放電）[%]", value=100.0, min_value=0.0, max_value=200.0, step=1.0)
limit_neg = st.sidebar.number_input("下限出力（充電）[%]", value=-100.0, min_value=-200.0, max_value=0.0, step=1.0)
invert_sign = st.sidebar.checkbox("符号を反転（+を充電、-を放電）", value=False)

# ---------------- 時間軸 ----------------
if unit_time.startswith("相対"):
    t0 = df_clean["time"].iloc[0]
    rel_sec = (df_clean["time"] - t0).to_numpy()
    time_display = pd.to_timedelta(rel_sec, unit="s")
else:
    time_display = df_clean["time"]

plot_base = pd.DataFrame({"time": time_display, "freq": df_clean["freq"]})
plot_base = plot_base.iloc[::resample, :].reset_index(drop=True)

# ---------------- Δf 算出 ----------------
delta_f_hz = df_clean["freq"] - f_center

# 不感帯適用（mHz -> Hz）
db_hz = deadband_mhz / 1000.0
def apply_deadband(x, db):
    if abs(x) <= db:
        return 0.0
    # DB外は、ゼロオフセット型（スロープはそのまま）
    if x > 0:
        return x - db
    else:
        return x + db

delta_after_db = delta_f_hz.apply(lambda v: apply_deadband(v, db_hz))

# ---------------- BESS 出力指令（%） ----------------
# ΔP/P = -(Δf / f_nom) / (droop_pct/100)
cmd_pu = - (delta_after_db / f_nom) / (droop_pct / 100.0)
cmd_percent = cmd_pu * 100.0  # %
if invert_sign:
    cmd_percent = -cmd_percent
# クリップ
cmd_percent = cmd_percent.clip(lower=limit_neg, upper=limit_pos)

# 表示用（Δf）
if dev_unit == "mHz":
    delta_display = delta_f_hz * 1000.0
    delta_ylabel = "偏差 Δf [mHz]"
else:
    delta_display = delta_f_hz
    delta_ylabel = "偏差 Δf [Hz]"

# ---------------- 統計 ----------------
st.subheader("概要・統計")
c1, c2 = st.columns(2)
with c1:
    st.write(f"データ点数：**{len(df_clean)}** / 平均周波数：**{df_clean['freq'].mean():.5f} Hz** / 中心周波数：**{f_center:.5f} Hz**")
with c2:
    st.write(f"Droop：**{droop_pct:.2f}%** / 不感帯：**{deadband_mhz:.1f} mHz** / 出力制限：**{limit_neg:.0f}% 〜 {limit_pos:.0f}%**")

# ---------------- グラフ（3枚）：周波数、Δf、BESS出力 ----------------
# 1) 周波数
fig_f = go.Figure()
fig_f.add_trace(go.Scatter(x=plot_base["time"], y=plot_base["freq"], mode="lines", name="周波数",
                           hovertemplate="時間=%{x}<br>周波数=%{y:.5f} Hz<extra></extra>"))
fig_f.add_hline(y=f_center, line=dict(width=1, dash="dot"), annotation_text="中心", annotation_position="top left")
fig_f.update_layout(margin=dict(l=20, r=20, t=40, b=40), title="周波数の時間変動",
                    xaxis_title="時間", yaxis_title="周波数 [Hz]", hovermode="x unified")

# 2) Δf
plot_df = pd.DataFrame({"time": time_display, "delta": delta_display})
plot_df = plot_df.iloc[::resample, :]
fig_d = go.Figure()
fig_d.add_trace(go.Scatter(x=plot_df["time"], y=plot_df["delta"], mode="lines", name="偏差 Δf",
                           hovertemplate="時間=%{x}<br>Δf=%{y:.3f}<extra></extra>"))
fig_d.add_hline(y=0.0, line=dict(width=1, dash="dash"))
fig_d.update_layout(margin=dict(l=20, r=20, t=40, b=40),
                    title=f"中心 {f_center:.5f} Hz からの偏差（Δf）",
                    xaxis_title="時間", yaxis_title=delta_ylabel, hovermode="x unified")

# 3) BESS 指令[%]
plot_cmd = pd.DataFrame({"time": time_display, "cmd": cmd_percent})
plot_cmd = plot_cmd.iloc[::resample, :]
fig_c = go.Figure()
fig_c.add_trace(go.Scatter(x=plot_cmd["time"], y=plot_cmd["cmd"], mode="lines", name="出力指令[%]",
                           hovertemplate="時間=%{x}<br>指令=%{y:.2f}%<extra></extra>"))
fig_c.add_hline(y=0.0, line=dict(width=1, dash="dash"))
fig_c.update_layout(margin=dict(l=20, r=20, t=40, b=40),
                    title="BESS 出力指令（Droop制御）", xaxis_title="時間", yaxis_title="出力指令 [%]",
                    hovermode="x unified", yaxis=dict(range=[min(limit_neg, -110), max(limit_pos, 110)]))

# 描画
st.plotly_chart(fig_f, use_container_width=True)
st.plotly_chart(fig_d, use_container_width=True)
st.plotly_chart(fig_c, use_container_width=True)

# プレビュー＆CSV
with st.expander("データ先頭をプレビュー（上位100行）"):
    preview = df_clean.copy()
    preview["delta_f(Hz)"] = delta_f_hz
    preview["delta_f_after_deadband(Hz)"] = delta_after_db
    preview["bess_cmd(%)"] = cmd_percent
    st.dataframe(preview.head(100))

csv_buf = io.StringIO()
out_df = df_clean.copy()
out_df["delta_f(Hz)"] = delta_f_hz
out_df["delta_f_after_deadband(Hz)"] = delta_after_db
out_df["bess_cmd(%)"] = cmd_percent
out_df.to_csv(csv_buf, index=False)
st.download_button("CSVをダウンロード（Δf・指令%含む）", data=csv_buf.getvalue().encode("utf-8"),
                   file_name="frequency_bess_response.csv", mime="text/csv")

st.markdown("---")
st.caption("符号の約束：周波数が下がると（Δf<0）出力指令は +%（放電）になります。必要なら「符号を反転」を有効化してください。")
