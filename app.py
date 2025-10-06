# app.py
# -*- coding: utf-8 -*-
"""
周波数×BESS応答 可視化 + エネルギー集計（AC/DC損失対応, AC基準符号）
- AC/DC符号を統一（放電:+, 充電:-）
- AC/DC効率のみを考慮
"""
import io
import re
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="BESS応答（AC/DC損失・AC基準符号）", page_icon="⚡", layout="wide")
st.title("周波数変動とBESS応答の可視化（AC/DC損失込み・AC基準符号）")

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

# サイドバー設定
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

# パラメータ
st.sidebar.header("BESSパラメータ")
f_nom = st.sidebar.number_input("系統公称周波数 [Hz]", value=50.0, step=0.1)
f_center = st.sidebar.number_input("中心周波数 [Hz]", value=float(df_clean["freq"].mean()), step=0.001)
droop_pct = st.sidebar.number_input("Droop [%]", value=5.0, step=0.1)
deadband_mhz = st.sidebar.number_input("不感帯 [mHz]", value=0.0, step=1.0)
rated_kw = st.sidebar.number_input("BESS定格出力 [kW]", value=1000.0, step=10.0)
eta_chg = st.sidebar.number_input("充電効率（AC→DC）[%]", value=96.0, step=0.1)
eta_dis = st.sidebar.number_input("放電効率（DC→AC）[%]", value=96.0, step=0.1)
target_hours = st.sidebar.number_input("換算時間 [h]", value=24.0, step=1.0)

# 時間変換
time_sec = df_clean["time"].to_numpy(dtype=float)
dt_sec = np.diff(time_sec, prepend=time_sec[0])
dt_sec = np.where(dt_sec < 0, 0.0, dt_sec)
dt_h = dt_sec / 3600.0

# Δf & deadband
delta_f = df_clean["freq"] - f_center
db_hz = deadband_mhz / 1000.0
delta_db = delta_f.apply(lambda v: 0 if abs(v)<=db_hz else (v-np.sign(v)*db_hz))

# 出力%
cmd_pu = - (delta_db / f_nom) / (droop_pct/100.0)
cmd_percent = np.clip(cmd_pu * 100, -100, 100)
p_ac = (cmd_percent/100) * rated_kw

# AC基準のまま効率補正
eta_chg_pu, eta_dis_pu = eta_chg/100.0, eta_dis/100.0
p_dc = np.where(
    p_ac >= 0,   # 放電
    p_ac / eta_dis_pu,
    p_ac * eta_chg_pu
)

# エネルギー積算
e_inc_ac = p_ac * dt_h
e_inc_dc = p_dc * dt_h
export_ac = np.sum(np.where(p_ac>0, p_ac*dt_h, 0))
import_ac = np.sum(np.where(p_ac<0, -p_ac*dt_h, 0))
dischg_dc = np.sum(np.where(p_dc>0, p_dc*dt_h, 0))
charge_dc = np.sum(np.where(p_dc<0, -p_dc*dt_h, 0))

duration_h = max((time_sec[-1]-time_sec[0])/3600.0, 1e-9)
scale = target_hours / duration_h

# グラフ
time_display = pd.to_timedelta(time_sec-time_sec[0], unit="s")
fig1 = go.Figure()
fig1.add_trace(go.Scatter(x=time_display, y=df_clean["freq"], mode="lines", name="Freq"))
fig1.add_hline(y=f_center, line=dict(dash="dot"), annotation_text="中心")
fig1.update_layout(title="周波数", yaxis_title="Hz")

fig2 = go.Figure()
fig2.add_trace(go.Scatter(x=time_display, y=cmd_percent, mode="lines", name="出力指令[%]"))
fig2.add_hline(y=0, line=dict(dash="dash"))
fig2.update_layout(title="BESS 出力指令[%]", yaxis_title="%")

fig3 = go.Figure()
fig3.add_trace(go.Scatter(x=time_display, y=p_ac, mode="lines", name="AC出力[kW]"))
fig3.add_trace(go.Scatter(x=time_display, y=p_dc, mode="lines", name="DC出力[kW]"))
fig3.add_hline(y=0, line=dict(dash="dash"))
fig3.update_layout(title="BESS 出力（AC/DC符号統一）", yaxis_title="kW")

st.plotly_chart(fig1, use_container_width=True)
st.plotly_chart(fig2, use_container_width=True)
st.plotly_chart(fig3, use_container_width=True)

# メトリクス
st.subheader("エネルギー指標")
c1,c2,c3=st.columns(3)
c1.metric("DC充電量（区間）", f"{charge_dc:.2f} kWh")
c2.metric("DC放電量（区間）", f"{dischg_dc:.2f} kWh")
c3.metric("期間", f"{duration_h:.2f} h")

c4,c5=st.columns(2)
c4.metric(f"DC充電量（換算{target_hours:.0f}h）", f"{charge_dc*scale:.2f} kWh/{target_hours:.0f}h")
c5.metric(f"DC放電量（換算{target_hours:.0f}h）", f"{dischg_dc*scale:.2f} kWh/{target_hours:.0f}h")

# CSV
csv_buf = io.StringIO()
pd.DataFrame({
    "time[s]": time_sec,
    "freq[Hz]": df_clean["freq"],
    "p_ac[kW]": p_ac,
    "p_dc[kW]": p_dc,
    "cmd_percent[%]": cmd_percent,
    "e_inc_ac[kWh]": e_inc_ac,
    "e_inc_dc[kWh]": e_inc_dc
}).to_csv(csv_buf, index=False)
st.download_button("CSVダウンロード", data=csv_buf.getvalue().encode("utf-8"), file_name="bess_acdc_v2.csv", mime="text/csv")

st.caption("符号をAC基準に統一：放電:+、充電:-。効率補正のみをDC側に適用。")
