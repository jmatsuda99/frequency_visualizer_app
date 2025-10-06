# app.py
# -*- coding: utf-8 -*-
"""
BESS可視化：AC/DC損失 + kWh集計 + SoC推移（容量/初期SoC% 指定）
- AC/DC符号は統一（放電:+, 充電:-）
- DC側は効率補正のみ（充電: *η_chg, 放電: /η_dis）
- BESS容量[kWh] と 初期SoC[%] をパラメータ化し、SoC[%]を時系列表示
"""
import io
import re
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="BESS応答（AC/DC + SoC）", page_icon="🔋", layout="wide")
st.title("周波数変動とBESS応答の可視化（AC/DC損失 + SoC）")

TIME_CANDIDATES = [r"time", r"時間", r"時刻", r"秒", r"sec", r"s", r"min", r"hour"]
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

# ---------------- 入力 ----------------
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
    st.info("ファイルをアップロードするか、『サンプルデータを読み込む』を押してください。")
    st.stop()

if sheets and len(sheets) > 1 and uploaded and uploaded.name.lower().endswith((".xlsx", ".xls")):
    sel_sheet = st.sidebar.selectbox("シート選択", sheets, index=sheets.index(sel_sheet))
    df, _, _ = read_file(uploaded, sheet_name=sel_sheet)

st.sidebar.subheader("列のマッピング")
suggest_time = _find_col(TIME_CANDIDATES, df.columns)
suggest_freq = _find_col(FREQ_CANDIDATES, df.columns)
col_time = st.sidebar.selectbox("時間列", [None]+list(df.columns), index=([None]+list(df.columns)).index(suggest_time) if suggest_time in df.columns else 0)
col_freq = st.sidebar.selectbox("周波数列", [None]+list(df.columns), index=([None]+list(df.columns)).index(suggest_freq) if suggest_freq in df.columns else 0)
if not col_time or not col_freq:
    st.error("時間列と周波数列を選択してください。")
    st.stop()

x = to_numeric_series(df[col_time])
y = to_numeric_series(df[col_freq])
mask = ~(x.isna() | y.isna())
df_clean = pd.DataFrame({"time": x[mask], "freq": y[mask]}).reset_index(drop=True)

# ---------------- パラメータ ----------------
st.sidebar.header("BESSパラメータ")
f_nom = st.sidebar.number_input("系統公称周波数 [Hz]", value=50.0, step=0.1)
f_center = st.sidebar.number_input("中心周波数 [Hz]", value=float(df_clean["freq"].mean()), step=0.001)
droop_pct = st.sidebar.number_input("Droop [%]", value=5.0, step=0.1)
deadband_mhz = st.sidebar.number_input("不感帯 [mHz]", value=0.0, step=1.0)
rated_kw = st.sidebar.number_input("BESS定格出力 [kW]", value=1000.0, step=10.0)
eta_chg = st.sidebar.number_input("充電効率（AC→DC）[%]", value=96.0, step=0.1)
eta_dis = st.sidebar.number_input("放電効率（DC→AC）[%]", value=96.0, step=0.1)

st.sidebar.header("エネルギー換算 & SoC")
target_hours = st.sidebar.number_input("換算時間 [h]", value=24.0, step=1.0)
capacity_kwh = st.sidebar.number_input("BESS 容量 [kWh]", value=2000.0, min_value=0.1, step=10.0)
soc0_pct = st.sidebar.number_input("初期 SoC [%]", value=50.0, min_value=0.0, max_value=100.0, step=1.0)
soc_clip = st.sidebar.checkbox("SoC を 0–100% にクリップ", value=True)

# ---------------- 時間・Δf・指令 ----------------
time_sec = df_clean["time"].to_numpy(dtype=float)
dt_sec = np.diff(time_sec, prepend=time_sec[0])
dt_sec = np.where(dt_sec < 0, 0.0, dt_sec)
dt_h = dt_sec / 3600.0

delta_f = df_clean["freq"] - f_center
db_hz = deadband_mhz / 1000.0
delta_db = delta_f.apply(lambda v: 0.0 if abs(v)<=db_hz else (v - np.sign(v)*db_hz))

cmd_pu = - (delta_db / f_nom) / (droop_pct/100.0)
cmd_percent = np.clip(cmd_pu * 100.0, -100.0, 100.0)
p_ac = (cmd_percent/100.0) * rated_kw  # 放電:+, 充電:-

# ---------------- AC→DC 換算（効率のみ、符号統一） ----------------
eta_chg_pu, eta_dis_pu = eta_chg/100.0, eta_dis/100.0
p_dc = np.where(
    p_ac >= 0.0,   # 放電
    p_ac / eta_dis_pu,
    p_ac * eta_chg_pu
)

# ---------------- エネルギー（AC/DC） ----------------
e_inc_ac = p_ac * dt_h       # kWh
e_inc_dc = p_dc * dt_h       # kWh （符号はACと同じ：放電+、充電-）

export_ac = float(np.sum(np.where(p_ac>0, p_ac*dt_h, 0.0)))
import_ac = float(np.sum(np.where(p_ac<0, -p_ac*dt_h, 0.0)))
dischg_dc = float(np.sum(np.where(p_dc>0, p_dc*dt_h, 0.0)))
charge_dc = float(np.sum(np.where(p_dc<0, -p_dc*dt_h, 0.0)))

duration_h = max((time_sec[-1]-time_sec[0]) / 3600.0, 1e-9)
scale = target_hours / duration_h

# ---------------- SoC 時系列 ----------------
# SoC 変化は「電池エネルギーの増減」に基づく：
#   放電（p_dc>0）→ 電池エネルギー減少 → SoC減少
#   充電（p_dc<0）→ 電池エネルギー増加 → SoC増加
# よって、電池エネルギーの増分（バッテリ視点）は -e_inc_dc
e_batt_inc = - e_inc_dc  # kWh
soc = np.empty_like(time_sec, dtype=float)
soc[0] = soc0_pct
for i in range(1, len(soc)):
    soc[i] = soc[i-1] + (e_batt_inc[i] / capacity_kwh) * 100.0

if soc_clip:
    soc = np.clip(soc, 0.0, 100.0)

# ---------------- グラフ ----------------
time_display = pd.to_timedelta(time_sec-time_sec[0], unit="s")

fig1 = go.Figure()
fig1.add_trace(go.Scatter(x=time_display, y=df_clean["freq"], mode="lines", name="Freq"))
fig1.add_hline(y=f_center, line=dict(dash="dot"), annotation_text="中心")
fig1.update_layout(title="周波数", xaxis_title="時間", yaxis_title="Hz", hovermode="x unified")

fig2 = go.Figure()
fig2.add_trace(go.Scatter(x=time_display, y=cmd_percent, mode="lines", name="出力指令[%]"))
fig2.add_hline(y=0, line=dict(dash="dash"))
fig2.update_layout(title="BESS 出力指令 [%]", xaxis_title="時間", yaxis_title="%", hovermode="x unified")

fig3 = go.Figure()
fig3.add_trace(go.Scatter(x=time_display, y=p_ac, mode="lines", name="AC出力[kW]"))
fig3.add_trace(go.Scatter(x=time_display, y=p_dc, mode="lines", name="DC出力[kW]"))
fig3.add_hline(y=0, line=dict(dash="dash"))
fig3.update_layout(title="BESS 出力（AC/DC）", xaxis_title="時間", yaxis_title="kW", hovermode="x unified")

fig4 = go.Figure()
fig4.add_trace(go.Scatter(x=time_display, y=soc, mode="lines", name="SoC [%]"))
fig4.add_hline(y=0, line=dict(dash="dot"))
fig4.add_hline(y=100, line=dict(dash="dot"))
fig4.update_layout(title="SoC の推移", xaxis_title="時間", yaxis_title="SoC [%]", hovermode="x unified")

st.plotly_chart(fig1, use_container_width=True)
st.plotly_chart(fig2, use_container_width=True)
st.plotly_chart(fig3, use_container_width=True)
st.plotly_chart(fig4, use_container_width=True)

# ---------------- メトリクス ----------------
st.subheader("エネルギー・SoC 指標")
c1,c2,c3 = st.columns(3)
c1.metric("DC 充電量（区間）", f"{charge_dc:,.2f} kWh")
c2.metric("DC 放電量（区間）", f"{dischg_dc:,.2f} kWh")
c3.metric("期間", f"{duration_h:.2f} h")

c4,c5,c6 = st.columns(3)
c4.metric(f"DC 充電（換算 {target_hours:.0f}h）", f"{charge_dc*scale:,.2f} kWh/{target_hours:.0f}h")
c5.metric(f"DC 放電（換算 {target_hours:.0f}h）", f"{dischg_dc*scale:,.2f} kWh/{target_hours:.0f}h")
c6.metric("最終 SoC", f"{soc[-1]:.2f} %")

c7,c8 = st.columns(2)
c7.metric("最小 SoC", f"{np.min(soc):.2f} %")
c8.metric("最大 SoC", f"{np.max(soc):.2f} %")

# ---------------- CSV ----------------
csv_buf = io.StringIO()
pd.DataFrame({
    "time[s]": time_sec,
    "freq[Hz]": df_clean["freq"],
    "cmd_percent[%]": cmd_percent,
    "p_ac[kW]": p_ac,
    "p_dc[kW]": p_dc,
    "e_inc_ac[kWh]": e_inc_ac,
    "e_inc_dc[kWh]": e_inc_dc,
    "soc[%]": soc
}).to_csv(csv_buf, index=False)
st.download_button("CSVダウンロード（SoC含む）", data=csv_buf.getvalue().encode("utf-8"),
                   file_name="bess_acdc_soc.csv", mime="text/csv")

st.caption("SoCは DC側エネルギー（放電:+ / 充電:-）に対し、電池視点の増分 -e_inc_dc を容量で正規化して更新しています。")
