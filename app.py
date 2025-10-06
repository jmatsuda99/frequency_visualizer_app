# app.py
# -*- coding: utf-8 -*-
"""
周波数×BESS応答 可視化 + エネルギー集計（AC/DC損失対応）
- Excel/CSV 読み込み（openpyxl）
- Droop制御 → 出力指令[%] → 出力[AC kW]
- **AC/DC 変換の損失を方向別（充電/放電）で設定**し、**DC側エネルギー[kWh]** を算出
- 換算時間[h]を指定してスケーリング（例：24h）
- ダウンロードは CSV のみ（画像DLなし）
"""
import io
import re
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="BESS応答（AC/DC損失・DC集計）", page_icon="🔌", layout="wide")
st.title("周波数変動とBESS応答の可視化（AC/DC損失込み・DC集計）")
st.caption("GitHub + Streamlit Cloud で動作 / 画像DLなし")

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

# ---------------- サイドバー：入力 ----------------
st.sidebar.header("データ入力")
uploaded = st.sidebar.file_uploader("Excel/CSV をアップロード", type=["xlsx", "xls", "csv"]) 
example_btn = st.sidebar.button("サンプルデータを読み込む")

if example_btn and uploaded is None:
    t = np.arange(1, 1501)  # 秒
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
x_raw = df[col_time]
y_raw = df[col_freq]
x = to_numeric_series(x_raw).rename("time_raw")
y = to_numeric_series(y_raw).rename("freq")
mask = ~(x.isna() | y.isna())
df_clean = pd.DataFrame({"time_raw": x[mask], "freq": y[mask]}).reset_index(drop=True)

# ---------------- パラメータ ----------------
st.sidebar.header("時間設定")
time_unit = st.sidebar.selectbox("時間列の単位", ["秒 (s)", "分 (min)", "時間 (h)"], index=0)
if time_unit.startswith("秒"):
    time_scale = 1.0
elif time_unit.startswith("分"):
    time_scale = 60.0
else:
    time_scale = 3600.0

st.sidebar.header("中心周波数・Δf")
auto_center = st.sidebar.checkbox("平均から自動設定", value=True)
if auto_center:
    f_center = float(df_clean["freq"].mean())
else:
    f_center = st.sidebar.number_input("中心周波数 [Hz]", value=50.0, step=0.001, format="%.3f")
dev_unit = st.sidebar.radio("偏差（Δf）の単位", ["mHz", "Hz"], index=0, horizontal=True)

st.sidebar.header("BESS 応答（Droop制御）")
f_nom = st.sidebar.number_input("系統公称周波数 [Hz]", value=50.0, step=0.1, format="%.1f")
droop_pct = st.sidebar.number_input("調停率 Droop [%]", value=5.0, min_value=0.1, step=0.1)
deadband_mhz = st.sidebar.number_input("不感帯 [mHz]", value=0.0, min_value=0.0, step=1.0)
limit_pos = st.sidebar.number_input("上限出力（放電）[%]", value=100.0, min_value=0.0, max_value=500.0, step=1.0)
limit_neg = st.sidebar.number_input("下限出力（充電）[%]", value=-100.0, min_value=-500.0, max_value=0.0, step=1.0)
invert_sign = st.sidebar.checkbox("符号を反転（+を充電、-を放電）", value=False)

st.sidebar.header("BESS 出力仕様")
rated_kw = st.sidebar.number_input("BESS 定格出力 [kW]", value=1000.0, min_value=0.0, step=10.0)

st.sidebar.header("エネルギー換算")
target_hours = st.sidebar.number_input("換算時間 [h]", value=24.0, min_value=0.1, step=1.0)

st.sidebar.header("AC/DC 変換効率（方向別）")
eta_chg = st.sidebar.number_input("充電（AC→DC）効率 η_chg [%]", value=96.0, min_value=50.0, max_value=100.0, step=0.1)
eta_dis = st.sidebar.number_input("放電（DC→AC）効率 η_dis [%]", value=96.0, min_value=50.0, max_value=100.0, step=0.1)

# ---------------- 時間軸とΔf ----------------
time_sec = df_clean["time_raw"].to_numpy(dtype=float) * time_scale
dt_sec = np.diff(time_sec, prepend=time_sec[0])
dt_sec = np.where(dt_sec < 0, 0.0, dt_sec)

delta_f_hz = df_clean["freq"] - f_center
db_hz = deadband_mhz / 1000.0
def apply_deadband(x, db):
    if abs(x) <= db:
        return 0.0
    return (x - db) if x > 0 else (x + db)
delta_after_db = delta_f_hz.apply(lambda v: apply_deadband(v, db_hz))

# 出力[%] → AC側 kW
cmd_pu = - (delta_after_db / f_nom) / (droop_pct / 100.0)
cmd_percent = cmd_pu * 100.0
if invert_sign:
    cmd_percent = -cmd_percent
cmd_percent = cmd_percent.clip(lower=limit_neg, upper=limit_pos)
p_ac_kw = (cmd_percent / 100.0) * rated_kw   # +放電 / -充電（AC側）

# ---------------- DC側換算（方向別効率） ----------------
eta_chg_pu = max(min(eta_chg / 100.0, 1.0), 0.0)
eta_dis_pu = max(min(eta_dis / 100.0, 1.0), 0.0)

# バッテリーDC側の符号規約：充電=+（SoC増）、放電=−（SoC減）
p_dc_kw = np.where(
    p_ac_kw >= 0,   # 放電（ACへ出す）
    - p_ac_kw / np.where(eta_dis_pu>0, eta_dis_pu, np.nan),
    - p_ac_kw * eta_chg_pu
)

# エネルギー（kWh）
dt_h = dt_sec / 3600.0
e_inc_dc_kwh = p_dc_kw * dt_h     # +充電 / −放電（DC側）

charge_dc_kwh = float(np.sum(np.where(e_inc_dc_kwh > 0,  e_inc_dc_kwh, 0.0)))  # 電池に入ったエネルギー
dischg_dc_kwh = float(np.sum(np.where(e_inc_dc_kwh < 0, -e_inc_dc_kwh, 0.0)))  # 電池から出たエネルギー

# 参考：AC側（グリッド視点）のエネルギー
e_inc_ac_kwh = p_ac_kw * dt_h
export_ac_kwh = float(np.sum(np.where(p_ac_kw > 0, p_ac_kw, 0.0) * dt_h))  # 系統へ出した
import_ac_kwh = float(np.sum(np.where(p_ac_kw < 0, -p_ac_kw, 0.0) * dt_h)) # 系統から取った

# 観測長と換算
duration_h = max((time_sec[-1] - time_sec[0]) / 3600.0, 1e-9)
scale = target_hours / duration_h
charge_dc_scaled = charge_dc_kwh * scale
dischg_dc_scaled = dischg_dc_kwh * scale

# ---------------- グラフ ----------------
time_display = pd.to_timedelta(time_sec - time_sec[0], unit="s")

# 周波数
plot_f = pd.DataFrame({"time": time_display, "freq": df_clean["freq"]})
fig1 = go.Figure()
fig1.add_trace(go.Scatter(x=plot_f["time"], y=plot_f["freq"], mode="lines", name="周波数"))
fig1.add_hline(y=f_center, line=dict(width=1, dash="dot"), annotation_text="中心", annotation_position="top left")
fig1.update_layout(title="周波数の時間変動", xaxis_title="時間", yaxis_title="周波数 [Hz]", hovermode="x unified")

# 出力指令[%]（AC）
plot_cmd = pd.DataFrame({"time": time_display, "cmd": cmd_percent})
fig2 = go.Figure()
fig2.add_trace(go.Scatter(x=plot_cmd["time"], y=plot_cmd["cmd"], mode="lines", name="出力指令[%]"))
fig2.add_hline(y=0.0, line=dict(width=1, dash="dash"))
fig2.update_layout(title="BESS 出力指令 [%]（AC側ベース）", xaxis_title="時間", yaxis_title="出力指令 [%]", hovermode="x unified")

# 出力（AC kW & DC kW）
plot_p = pd.DataFrame({"time": time_display, "p_ac[kW]": p_ac_kw, "p_dc[kW]": p_dc_kw})
fig3 = go.Figure()
fig3.add_trace(go.Scatter(x=plot_p["time"], y=plot_p["p_ac[kW]"], mode="lines", name="AC出力[kW]"))
fig3.add_trace(go.Scatter(x=plot_p["time"], y=plot_p["p_dc[kW]"], mode="lines", name="DC出力[kW]（電池符号）"))
fig3.add_hline(y=0.0, line=dict(width=1, dash="dash"))
fig3.update_layout(title="BESS 出力（AC/DC換算）", xaxis_title="時間", yaxis_title="出力 [kW]", hovermode="x unified")

st.plotly_chart(fig1, use_container_width=True)
st.plotly_chart(fig2, use_container_width=True)
st.plotly_chart(fig3, use_container_width=True)

# ---------------- 指標の表示 ----------------
st.subheader("エネルギー指標（AC/DC・換算）")
c1, c2, c3 = st.columns(3)
with c1:
    st.metric("DC 充電（区間合計）", f"{charge_dc_kwh:,.2f} kWh")
with c2:
    st.metric("DC 放電（区間合計）", f"{dischg_dc_kwh:,.2f} kWh")
with c3:
    st.metric("観測区間の長さ", f"{duration_h:.2f} h")

c4, c5 = st.columns(2)
with c4:
    st.metric(f"DC 充電（換算 {target_hours:.1f} h）", f"{charge_dc_scaled:,.2f} kWh/{target_hours:.0f}h")
with c5:
    st.metric(f"DC 放電（換算 {target_hours:.1f} h）", f"{dischg_dc_scaled:,.2f} kWh/{target_hours:.0f}h")

with st.expander("参考：AC側エネルギー（系統視点）"):
    c6, c7 = st.columns(2)
    with c6:
        st.metric("AC 輸入（区間合計, 充電）", f"{import_ac_kwh:,.2f} kWh")
    with c7:
        st.metric("AC 輸出（区間合計, 放電）", f"{export_ac_kwh:,.2f} kWh")

# ---------------- プレビュー＆CSV ----------------
with st.expander("データ先頭をプレビュー（上位100行）"):
    preview = pd.DataFrame({
        "time[s]": time_sec,
        "freq[Hz]": df_clean["freq"],
        "cmd_percent[%]": cmd_percent,
        "p_ac[kW]": p_ac_kw,
        "p_dc[kW]": p_dc_kw,
        "dt[h]": dt_h,
        "e_inc_ac[kWh]": e_inc_ac_kwh,
        "e_inc_dc[kWh]": e_inc_dc_kwh,
    })
    st.dataframe(preview.head(100))

csv_buf = io.StringIO()
out_df = pd.DataFrame({
    "time[s]": time_sec,
    "freq[Hz]": df_clean["freq"],
    "cmd_percent[%]": cmd_percent,
    "p_ac[kW]": p_ac_kw,
    "p_dc[kW]": p_dc_kw,
    "dt[h]": dt_h,
    "e_inc_ac[kWh]": e_inc_ac_kwh,
    "e_inc_dc[kWh]": e_inc_dc_kwh,
})
out_df.to_csv(csv_buf, index=False)
st.download_button("CSVをダウンロード（AC/DC エネルギー含む）", data=csv_buf.getvalue().encode("utf-8"),
                   file_name="bess_acdc_energy.csv", mime="text/csv")

st.markdown("---")
st.caption("符号規約：AC側 + は系統へ放電、− は系統から充電。DC側は + が電池に入る（充電）、− が電池から出る（放電）。方向別効率で損失を考慮して換算しています。")
