# app.py
# -*- coding: utf-8 -*-
"""
BESS可視化：AC/DC損失 + SoC + AC端結果
- 指標を「表形式」で見やすく表示（区間合計・換算値の2列）
- トグルで「メトリクス表示」⇄「表表示」を切替
"""
import io
import re
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="BESS応答（AC/DC + SoC + 表）", page_icon="📊", layout="wide")
st.title("周波数変動とBESS応答の可視化（AC/DC損失 + SoC + 表サマリ）")

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
u = st.sidebar.file_uploader("Excel/CSV をアップロード", type=["xlsx","xls","csv"])
ex = st.sidebar.button("サンプルデータを読み込む")
if ex and u is None:
    t = np.arange(1, 1501)
    base = 50 + 0.01*np.sin(2*np.pi*t/300) + 0.005*np.sin(2*np.pi*t/35)
    noise = np.random.normal(0, 0.003, size=t.size)
    df = pd.DataFrame({"time(s)": t, "Frequency(Hz)": base + noise})
    sheets = ["sample"]; sel = "sample"
else:
    if u:
        df, sheets, sel = read_file(u)
    else:
        df = sheets = sel = None

if df is None:
    st.info("ファイルをアップロードするか、『サンプルデータを読み込む』を押してください。")
    st.stop()

if sheets and len(sheets)>1 and u and u.name.lower().endswith((".xlsx",".xls")):
    sel = st.sidebar.selectbox("シート選択", sheets, index=sheets.index(sel))
    df, _, _ = read_file(u, sheet_name=sel)

st.sidebar.subheader("列のマッピング")
tc = _find_col(TIME_CANDS, df.columns); fc = _find_col(FREQ_CANDS, df.columns)
col_t = st.sidebar.selectbox("時間列", [None]+list(df.columns), index=([None]+list(df.columns)).index(tc) if tc in df.columns else 0)
col_f = st.sidebar.selectbox("周波数列", [None]+list(df.columns), index=([None]+list(df.columns)).index(fc) if fc in df.columns else 0)
if not col_t or not col_f:
    st.error("時間列と周波数列を選択してください。"); st.stop()

x = to_num(df[col_t]); y = to_num(df[col_f]); m = ~(x.isna() | y.isna())
dfc = pd.DataFrame({"time": x[m], "freq": y[m]}).reset_index(drop=True)

# ---------------- パラメータ ----------------
st.sidebar.header("BESSパラメータ")
f_nom = st.sidebar.number_input("系統公称周波数 [Hz]", value=50.0, step=0.1)
f_ctr = st.sidebar.number_input("中心周波数 [Hz]", value=float(dfc["freq"].mean()), step=0.001)
droop = st.sidebar.number_input("Droop [%]", value=5.0, step=0.1)
db_mhz = st.sidebar.number_input("不感帯 [mHz]", value=0.0, step=1.0)
rated = st.sidebar.number_input("BESS定格出力 [kW]", value=1000.0, step=10.0)
eta_chg = st.sidebar.number_input("充電効率（AC→DC）[%]", value=96.0, step=0.1)
eta_dis = st.sidebar.number_input("放電効率（DC→AC）[%]", value=96.0, step=0.1)

st.sidebar.header("エネルギー換算 & SoC")
target_h = st.sidebar.number_input("換算時間 [h]", value=24.0, step=1.0)
capacity = st.sidebar.number_input("BESS 容量 [kWh]", value=2000.0, min_value=0.1, step=10.0)
soc0 = st.sidebar.number_input("初期 SoC [%]", value=50.0, min_value=0.0, max_value=100.0, step=1.0)
clip_soc = st.sidebar.checkbox("SoC を 0–100% にクリップ", value=True)

view_mode = st.sidebar.radio("サマリ表示モード", ["表（おすすめ）", "メトリクス"], horizontal=True)

# ---------------- 計算 ----------------
tsec = dfc["time"].to_numpy(float); dt = np.diff(tsec, prepend=tsec[0]); dt = np.where(dt<0, 0.0, dt); dth = dt/3600.0
delta = dfc["freq"] - f_ctr; db = db_mhz/1000.0; delta_db = delta.apply(lambda v: 0.0 if abs(v)<=db else (v - np.sign(v)*db))
cmd_pu = - (delta_db / f_nom) / (droop/100.0); cmd = np.clip(cmd_pu*100.0, -100.0, 100.0)
p_ac = (cmd/100.0)*rated
eta_c, eta_d = eta_chg/100.0, eta_dis/100.0
p_dc = np.where(p_ac>=0, p_ac/eta_d, p_ac*eta_c)

e_ac = p_ac * dth; e_dc = p_dc * dth
export_ac = float(np.sum(np.where(p_ac>0,  p_ac*dth, 0.0)))
import_ac = float(np.sum(np.where(p_ac<0, -p_ac*dth, 0.0)))
dis_dc    = float(np.sum(np.where(p_dc>0,  p_dc*dth, 0.0)))
chg_dc    = float(np.sum(np.where(p_dc<0, -p_dc*dth, 0.0)))

dur = max((tsec[-1]-tsec[0])/3600.0, 1e-9); scale = target_h/dur

# SoC
e_batt_inc = - e_dc
soc = np.empty_like(tsec); soc[0]=soc0
for i in range(1,len(soc)): soc[i] = soc[i-1] + (e_batt_inc[i]/capacity)*100.0
if clip_soc: soc = np.clip(soc, 0.0, 100.0)

# ---------------- グラフ ----------------
td = pd.to_timedelta(tsec-tsec[0], unit="s")
fig1 = go.Figure(); fig1.add_trace(go.Scatter(x=td,y=dfc["freq"],mode="lines",name="Freq")); fig1.add_hline(y=f_ctr,line=dict(dash="dot"),annotation_text="中心")
fig1.update_layout(title="周波数", xaxis_title="時間", yaxis_title="Hz", hovermode="x unified")
fig2 = go.Figure(); fig2.add_trace(go.Scatter(x=td,y=cmd,mode="lines",name="出力指令[%]")); fig2.add_hline(y=0,line=dict(dash="dash"))
fig2.update_layout(title="BESS 出力指令 [%]", xaxis_title="時間", yaxis_title="%", hovermode="x unified")
fig3 = go.Figure(); fig3.add_trace(go.Scatter(x=td,y=p_ac,mode="lines",name="AC出力[kW]")); fig3.add_trace(go.Scatter(x=td,y=p_dc,mode="lines",name="DC出力[kW]")); fig3.add_hline(y=0,line=dict(dash="dash"))
fig3.update_layout(title="BESS 出力（AC/DC）", xaxis_title="時間", yaxis_title="kW", hovermode="x unified")
fig4 = go.Figure(); fig4.add_trace(go.Scatter(x=td,y=soc,mode="lines",name="SoC[%]")); fig4.add_hline(y=0,line=dict(dash="dot")); fig4.add_hline(y=100,line=dict(dash="dot"))
fig4.update_layout(title="SoC の推移", xaxis_title="時間", yaxis_title="SoC [%]", hovermode="x unified")
st.plotly_chart(fig1, use_container_width=True); st.plotly_chart(fig2, use_container_width=True); st.plotly_chart(fig3, use_container_width=True); st.plotly_chart(fig4, use_container_width=True)

# ---------------- サマリ表 or メトリクス ----------------
st.subheader("エネルギー指標（AC端・DC端）")
if view_mode.startswith("表"):
    summary = pd.DataFrame({
        "区間合計 [kWh]": [export_ac, import_ac, dis_dc, chg_dc, export_ac-import_ac, dis_dc-chg_dc],
        f"換算 {target_h:.0f}h [kWh/{int(target_h)}h]": [export_ac*scale, import_ac*scale, dis_dc*scale, chg_dc*scale, (export_ac-import_ac)*scale, (dis_dc-chg_dc)*scale],
    }, index=["AC 輸出", "AC 輸入", "DC 放電", "DC 充電", "AC ネット（輸出-輸入）", "DC ネット（放電-充電）"])
    st.dataframe(summary.style.format("{:,.2f}"))
else:
    c1,c2,c3=st.columns(3)
    c1.metric("AC 輸出（区間）", f"{export_ac:,.2f} kWh"); c2.metric("AC 輸入（区間）", f"{import_ac:,.2f} kWh"); c3.metric("期間", f"{dur:.2f} h")
    c4,c5,c6,c7=st.columns(4)
    c4.metric(f"AC 輸出（換算 {target_h:.0f}h）", f"{export_ac*scale:,.2f} kWh/{target_h:.0f}h")
    c5.metric(f"AC 輸入（換算 {target_h:.0f}h）", f"{import_ac*scale:,.2f} kWh/{target_h:.0f}h")
    c6.metric(f"DC 放電（区間）", f"{dis_dc:,.2f} kWh"); c7.metric(f"DC 充電（区間）", f"{chg_dc:,.2f} kWh")
    c8,c9=st.columns(2)
    c8.metric(f"DC 放電（換算 {target_h:.0f}h）", f"{dis_dc*scale:,.2f} kWh/{target_h:.0f}h")
    c9.metric(f"DC 充電（換算 {target_h:.0f}h）", f"{chg_dc*scale:,.2f} kWh/{target_h:.0f}h")

# ---------------- CSV ----------------
csv_buf = io.StringIO()
pd.DataFrame({
    "time[s]": tsec, "freq[Hz]": dfc["freq"],
    "cmd_percent[%]": cmd, "p_ac[kW]": p_ac, "p_dc[kW]": p_dc,
    "e_inc_ac[kWh]": e_ac, "e_inc_dc[kWh]": e_dc, "soc[%]": soc
}).to_csv(csv_buf, index=False)
st.download_button("CSVダウンロード（AC/DC・SoC）", data=csv_buf.getvalue().encode("utf-8"), file_name="bess_acdc_soc_table.csv", mime="text/csv")

st.caption("サマリは表とメトリクスを切替可。ネット値（輸出-輸入/放電-充電）も併記。")
