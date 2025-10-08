# app.py
# -*- coding: utf-8 -*-
"""
BESS可視化：AC/DC損失 + SoC + 表サマリ ＋ Δf時系列/ヒストグラム（±σライン）
- 既存機能を維持しつつ、Δfヒストグラム直下に「描画用データ（Δf配列）」のダウンロードを追加
"""
import io
import re
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="BESS応答（AC/DC + SoC + 表 + Δf±σ）", page_icon="📊", layout="wide")
st.title("周波数×BESS応答（AC/DC損失 + SoC + 表サマリ + Δf±σ）")

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
# 中心周波数は既定でデータ平均。手動で微調整も可。
f_ctr = st.sidebar.number_input("中心周波数 [Hz]", value=float(dfc["freq"].mean()), step=0.001, help="既定はデータ平均。Δfと制御はこの値基準。")
droop = st.sidebar.number_input("Droop [%]", value=5.0, step=0.1)
db_mhz = st.sidebar.number_input("不感帯 [mHz]", value=0.0, step=1.0)
rated = st.sidebar.number_input("BESS定格出力 [kW]", value=1000.0, step=10.0)
eta_chg = st.sidebar.number_input("充電効率（AC→DC）[%]", value=96.0, step=0.1)
eta_dis = st.sidebar.number_input("放電効率（DC→AC）[%]", value=96.0, step=0.1)

# ==== Deadband内動作モード（A/B） ====
st.sidebar.subheader("Deadband内の動作")
db_mode = st.sidebar.selectbox(
    "Deadband Mode",
    options=["HOLD", "SOC_STEPS"],
    index=0,
    help="HOLD: DB内は0%。SOC_STEPS: SoC帯域ごとに固定出力（% of rated）。"
)

# 帯域境界（降順, %）と各帯域の固定出力（% of rated）
soc_band_edges_str = st.sidebar.text_input(
    "SoC帯域の境界（%）降順、カンマ区切り",
    value="90,75,50,30"
)
db_outputs_pct_str = st.sidebar.text_input(
    "各帯域のDB内出力（% of rated）カンマ区切り",
    value="9,7,0,-7,-9"
)

def _parse_csv_floats(s):
    if not s.strip():
        return []
    return [float(x.strip()) for x in s.split(",") if x.strip()]

soc_band_edges = _parse_csv_floats(soc_band_edges_str)
db_outputs_pct = _parse_csv_floats(db_outputs_pct_str)

# 妥当性チェック（軽微）
if sorted(soc_band_edges, reverse=True) != soc_band_edges:
    st.warning("SoC帯域境界は降順（大→小）で入力してください。")
if len(db_outputs_pct) != (len(soc_band_edges) + 1):
    st.warning("帯域数（境界+1）とDB内出力の個数が一致していません。")

st.sidebar.header("エネルギー換算 & SoC")
target_h = st.sidebar.number_input("換算時間 [h]", value=24.0, step=1.0)
capacity = st.sidebar.number_input("BESS 容量 [kWh]", value=2000.0, min_value=0.1, step=10.0)
soc0 = st.sidebar.number_input("初期 SoC [%]", value=50.0, min_value=0.0, max_value=100.0, step=1.0)
clip_soc = st.sidebar.checkbox("SoC を 0–100% にクリップ", value=True)

view_mode = st.sidebar.radio("サマリ表示モード", ["表（おすすめ）", "メトリクス"], horizontal=True)

# ---------------- 
def compute_db_power_pct_soc_steps(soc_pct, edges_desc, outputs_pct_desc):
    """SoC[%]に基づく段階ステップ出力（% of rated）を返す。境界は降順リスト。"""
    prev = 100.0
    for i, edge in enumerate(edges_desc):
        if soc_pct <= prev and soc_pct > edge:
            return outputs_pct_desc[i]
        prev = edge
    return outputs_pct_desc[-1] if outputs_pct_desc else 0.0

# 計算（BESS制御） ----------------
tsec = dfc["time"].to_numpy(float); dt = np.diff(tsec, prepend=tsec[0]); dt = np.where(dt<0, 0.0, dt); dth = dt/3600.0
delta = dfc["freq"] - f_ctr; db = db_mhz/1000.0
delta_db = delta.apply(lambda v: 0.0 if abs(v)<=db else (v - np.sign(v)*db))

cmd_pu = - (delta_db / f_nom) / (droop/100.0)
cmd = np.clip(cmd_pu*100.0, -100.0, 100.0)  # %
p_ac = (cmd/100.0)*rated                   # 放電:+, 充電:-

eta_c, eta_d = eta_chg/100.0, eta_dis/100.0
p_dc = np.where(p_ac>=0, p_ac/eta_d, p_ac*eta_c)

e_ac = p_ac*dth; e_dc = p_dc*dth
export_ac = float(np.sum(np.where(p_ac>0,  p_ac*dth, 0.0)))
import_ac = float(np.sum(np.where(p_ac<0, -p_ac*dth, 0.0)))
dis_dc    = float(np.sum(np.where(p_dc>0,  p_dc*dth, 0.0)))
chg_dc    = float(np.sum(np.where(p_dc<0, -p_dc*dth, 0.0)))

dur = max((tsec[-1]-tsec[0])/3600.0, 1e-9); scale = target_h/dur

# SoC（DC側エネルギーで更新）
e_batt_inc = - e_dc
soc = np.empty_like(tsec); soc[0]=soc0
for i in range(1,len(soc)): soc[i] = soc[i-1] + (e_batt_inc[i]/capacity)*100.0
if clip_soc: soc = np.clip(soc, 0.0, 100.0)

# ==== SOC_STEPS モードのときは、DB内をSoC帯域の固定出力で逐次再計算 ====
if db_mode == "SOC_STEPS" and len(db_outputs_pct)==(len(soc_band_edges)+1):
    droop_cmd_pct_vec = cmd.copy()
    cmd_seq = np.zeros_like(droop_cmd_pct_vec)
    p_ac_seq = np.zeros_like(droop_cmd_pct_vec, dtype=float)
    p_dc_seq = np.zeros_like(droop_cmd_pct_vec, dtype=float)
    e_ac_seq = np.zeros_like(droop_cmd_pct_vec, dtype=float)
    e_dc_seq = np.zeros_like(droop_cmd_pct_vec, dtype=float)
    soc_seq = np.empty_like(tsec, dtype=float); soc_seq[0] = soc0

    db_hz = db_mhz/1000.0

    for i in range(len(tsec)):
        df_i = (dfc["freq"].iat[i] - f_ctr)
        in_db = (abs(df_i) <= db_hz)
        if in_db:
            out_pct = compute_db_power_pct_soc_steps(soc_seq[i], soc_band_edges, db_outputs_pct)
        else:
            out_pct = droop_cmd_pct_vec[i]

        p_ac_i = (out_pct/100.0) * rated
        if p_ac_i >= 0:
            p_dc_i = p_ac_i / (eta_dis/100.0)
        else:
            p_dc_i = p_ac_i * (eta_chg/100.0)

        e_ac_i = p_ac_i * dth[i]
        e_dc_i = p_dc_i * dth[i]

        cmd_seq[i] = out_pct
        p_ac_seq[i] = p_ac_i
        p_dc_seq[i] = p_dc_i
        e_ac_seq[i] = e_ac_i
        e_dc_seq[i] = e_dc_i

        if i < len(tsec)-1:
            soc_next = soc_seq[i] + (-e_dc_i/ capacity)*100.0
            soc_seq[i+1] = np.clip(soc_next, 0.0, 100.0) if clip_soc else soc_next

    cmd = cmd_seq
    p_ac = p_ac_seq; p_dc = p_dc_seq
    e_ac = e_ac_seq; e_dc = e_dc_seq
    soc = soc_seq
# ==== KPIを再計算（DB内動作が有効な場合も反映） ====
e_ac = p_ac * dth
e_dc = p_dc * dth
export_ac = float(np.sum(np.where(p_ac > 0,  p_ac * dth, 0.0)))
import_ac = float(np.sum(np.where(p_ac < 0, -p_ac * dth, 0.0)))
dis_dc    = float(np.sum(np.where(p_dc > 0,  p_dc * dth, 0.0)))
chg_dc    = float(np.sum(np.where(p_dc < 0, -p_dc * dth, 0.0)))
    

# ---------------- 既存グラフ（周波数, 出力, SoC） ----------------
td = pd.to_timedelta(tsec-tsec[0], unit="s")
fig1 = go.Figure(); fig1.add_trace(go.Scatter(x=td,y=dfc["freq"],mode="lines",name="Freq"))
fig1.add_hline(y=f_ctr, line=dict(dash="dot"), annotation_text="中心")
fig1.update_layout(title="周波数", xaxis_title="時間", yaxis_title="Hz", hovermode="x unified")

fig2 = go.Figure(); fig2.add_trace(go.Scatter(x=td,y=cmd,mode="lines",name="出力指令[%]"))
fig2.add_hline(y=0,line=dict(dash="dash"))
fig2.update_layout(title="BESS 出力指令 [%]", xaxis_title="時間", yaxis_title="%", hovermode="x unified")

fig3 = go.Figure(); fig3.add_trace(go.Scatter(x=td,y=p_ac,mode="lines",name="AC出力[kW]"))
fig3.add_trace(go.Scatter(x=td,y=p_dc,mode="lines",name="DC出力[kW]"))
fig3.add_hline(y=0,line=dict(dash="dash"))
fig3.update_layout(title="BESS 出力（AC/DC）", xaxis_title="時間", yaxis_title="kW", hovermode="x unified")

fig4 = go.Figure(); fig4.add_trace(go.Scatter(x=td,y=soc,mode="lines",name="SoC[%]"))
fig4.add_hline(y=0,line=dict(dash="dot")); fig4.add_hline(y=100,line=dict(dash="dot"))
fig4.update_layout(title="SoC の推移", xaxis_title="時間", yaxis_title="SoC [%]", hovermode="x unified")

# ---------------- 追加：Δf 時系列 & ヒストグラム（±σ） ----------------
delta_f = (dfc["freq"] - f_ctr).to_numpy()
sigma = float(np.std(delta_f, ddof=1))  # 標本標準偏差

fig_dev = go.Figure()
fig_dev.add_trace(go.Scatter(x=td, y=delta_f, mode="lines", name="Δf [Hz]"))
fig_dev.add_hline(y=0.0, line=dict(dash="dash"),
                  annotation_text=f"中心 = {f_ctr:.5f} Hz（Δf=0）", annotation_position="top left")
for n in (1,2,3):
    yline = n*sigma
    fig_dev.add_hline(y= yline, line=dict(dash="dot"), annotation_text=f"+{n}σ = { yline:+.6f} Hz", annotation_position="top left")
    fig_dev.add_hline(y=-yline, line=dict(dash="dot"), annotation_text=f"-{n}σ = {-yline:+.6f} Hz", annotation_position="bottom left")
fig_dev.update_layout(title="周波数偏差（Δf）と ±1σ/2σ/3σ", xaxis_title="時間", yaxis_title="Δf [Hz]", hovermode="x unified")

fig_hist = go.Figure()
fig_hist.add_trace(go.Histogram(x=delta_f, nbinsx=60, name="Δf 分布", opacity=0.8))
fig_hist.add_vline(x=0.0, line=dict(dash="dash"), annotation_text="中心 (Δf=0)", annotation_position="top left")
for n in (1,2,3):
    xline = n*sigma
    fig_hist.add_vline(x= xline, line=dict(dash="dot"), annotation_text=f"+{n}σ = { xline:+.6f} Hz", annotation_position="top left")
    fig_hist.add_vline(x=-xline, line=dict(dash="dot"), annotation_text=f"-{n}σ = {-xline:+.6f} Hz", annotation_position="bottom left")
fig_hist.update_layout(title="周波数偏差（Δf）ヒストグラム（±1σ/2σ/3σ）", xaxis_title="Δf [Hz]", yaxis_title="度数", bargap=0.02)

# 描画
st.plotly_chart(fig1, use_container_width=True)
st.plotly_chart(fig2, use_container_width=True)
st.plotly_chart(fig3, use_container_width=True)
st.plotly_chart(fig4, use_container_width=True)
st.plotly_chart(fig_dev, use_container_width=True)
st.plotly_chart(fig_hist, use_container_width=True)

# ▼▼ ヒストグラム描画データのダウンロード（Δf配列） ▼▼
hist_csv = io.StringIO()
pd.DataFrame({"delta_f[Hz]": delta_f}).to_csv(hist_csv, index=False)
st.download_button(
    "ヒストグラム描画用データ（Δf配列）をダウンロード",
    data=hist_csv.getvalue().encode("utf-8"),
    file_name="histogram_delta_f_data.csv",
    mime="text/csv",
    help="このCSVはヒストグラム作成に使った Δf の生データ（1列）です。"
)

# ---------------- サマリ（表 or メトリクス） ----------------
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

# ---------------- CSV（総合） ----------------
csv_buf = io.StringIO()
pd.DataFrame({
    "time[s]": tsec, "freq[Hz]": dfc["freq"],
    "cmd_percent[%]": cmd,
    "p_ac[kW]": p_ac, "p_dc[kW]": p_dc,
    "e_inc_ac[kWh]": e_ac, "e_inc_dc[kWh]": e_dc,
    "soc[%]": soc,
    "delta_f[Hz]": delta_f
}).to_csv(csv_buf, index=False)
st.download_button("CSVダウンロード（AC/DC・SoC・Δf）", data=csv_buf.getvalue().encode("utf-8"),
                   file_name="bess_acdc_soc_with_deltaf.csv", mime="text/csv")

st.caption("Δfは『中心周波数』入力を基準に算出。σは標本標準偏差（ddof=1）。ヒストグラム直下のCSVはグラフ描画に使ったΔfそのものです。")
