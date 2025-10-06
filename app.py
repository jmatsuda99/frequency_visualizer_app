# app.py
# -*- coding: utf-8 -*-
import io, re, numpy as np, pandas as pd, plotly.graph_objects as go, streamlit as st
st.set_page_config(page_title='BESS応答（AC/DC + SoC + AC結果）', page_icon='🔋', layout='wide')
st.title('周波数変動とBESS応答の可視化（AC/DC損失 + SoC + AC端結果）')
TIME_CANDS=[r'time',r'時間',r'時刻',r'秒',r'sec',r's',r'min',r'hour']; FREQ_CANDS=[r'freq',r'周波数',r'frequency',r'hz']
def _find_col(cands, cols): import re; p=re.compile(r'|'.join(cands), re.I); h=[c for c in cols if p.search(str(c))]; return h[0] if h else None
@st.cache_data(show_spinner=False)
def read_file(file, sheet_name=None):
    name=getattr(file,'name','uploaded')
    if name.lower().endswith(('.xlsx','.xls')):
        xls=pd.ExcelFile(file); sheets=xls.sheet_names
        if sheet_name is None: sheet_name=sheets[0]
        df=xls.parse(sheet_name); return df, sheets, sheet_name
    else:
        df=pd.read_csv(file); return df, ['(CSV)'], '(CSV)'
def to_num(s): 
    try: return pd.to_numeric(s, errors='coerce')
    except: return pd.Series(dtype=float)

st.sidebar.header('データ入力')
u=st.sidebar.file_uploader('Excel/CSV をアップロード', type=['xlsx','xls','csv'])
ex=st.sidebar.button('サンプルデータを読み込む')
if ex and u is None:
    t=np.arange(1,1501); base=50+0.01*np.sin(2*np.pi*t/300)+0.005*np.sin(2*np.pi*t/35); noise=np.random.normal(0,0.003,size=t.size)
    df=pd.DataFrame({'time(s)':t,'Frequency(Hz)':base+noise}); sheets=['sample']; sel='sample'
else:
    if u: df,sheets,sel=read_file(u)
    else: df=sheets=sel=None
if df is None:
    st.info('ファイルをアップロードするか、『サンプルデータを読み込む』を押してください。'); st.stop()
if sheets and len(sheets)>1 and u and u.name.lower().endswith(('.xlsx','.xls')):
    sel=st.sidebar.selectbox('シート選択', sheets, index=sheets.index(sel)); df,_,_=read_file(u, sheet_name=sel)

st.sidebar.subheader('列のマッピング')
tc=_find_col(TIME_CANDS, df.columns); fc=_find_col(FREQ_CANDS, df.columns)
col_t=st.sidebar.selectbox('時間列',[None]+list(df.columns), index=([None]+list(df.columns)).index(tc) if tc in df.columns else 0)
col_f=st.sidebar.selectbox('周波数列',[None]+list(df.columns), index=([None]+list(df.columns)).index(fc) if fc in df.columns else 0)
if not col_t or not col_f: st.error('時間列と周波数列を選択してください。'); st.stop()

x=to_num(df[col_t]); y=to_num(df[col_f]); m=~(x.isna()|y.isna()); dfc=pd.DataFrame({'time':x[m],'freq':y[m]}).reset_index(drop=True)

st.sidebar.header('BESSパラメータ')
f_nom=st.sidebar.number_input('系統公称周波数 [Hz]', value=50.0, step=0.1)
f_ctr=st.sidebar.number_input('中心周波数 [Hz]', value=float(dfc['freq'].mean()), step=0.001)
droop=st.sidebar.number_input('Droop [%]', value=5.0, step=0.1)
db_mhz=st.sidebar.number_input('不感帯 [mHz]', value=0.0, step=1.0)
rated=st.sidebar.number_input('BESS定格出力 [kW]', value=1000.0, step=10.0)
eta_chg=st.sidebar.number_input('充電効率（AC→DC）[%]', value=96.0, step=0.1)
eta_dis=st.sidebar.number_input('放電効率（DC→AC）[%]', value=96.0, step=0.1)

st.sidebar.header('エネルギー換算 & SoC')
target_h=st.sidebar.number_input('換算時間 [h]', value=24.0, step=1.0)
cap=st.sidebar.number_input('BESS 容量 [kWh]', value=2000.0, min_value=0.1, step=10.0)
soc0=st.sidebar.number_input('初期 SoC [%]', value=50.0, min_value=0.0, max_value=100.0, step=1.0)
clip_soc=st.sidebar.checkbox('SoC を 0–100% にクリップ', value=True)

tsec=dfc['time'].to_numpy(float); dt=np.diff(tsec, prepend=tsec[0]); dt=np.where(dt<0,0.0,dt); dth=dt/3600.0
delta=dfc['freq']-f_ctr; db=db_mhz/1000.0; delta_db=delta.apply(lambda v:0.0 if abs(v)<=db else (v-np.sign(v)*db))
cmd_pu=-(delta_db/f_nom)/(droop/100.0); cmd=np.clip(cmd_pu*100.0, -100.0, 100.0)
p_ac=(cmd/100.0)*rated
eta_c,eta_d=eta_chg/100.0, eta_dis/100.0
p_dc=np.where(p_ac>=0, p_ac/eta_d, p_ac*eta_c)

e_ac=p_ac*dth; e_dc=p_dc*dth
export_ac=float(np.sum(np.where(p_ac>0, p_ac*dth, 0.0)))
import_ac=float(np.sum(np.where(p_ac<0,-p_ac*dth, 0.0)))
dis_dc=float(np.sum(np.where(p_dc>0, p_dc*dth, 0.0)))
chg_dc=float(np.sum(np.where(p_dc<0,-p_dc*dth, 0.0)))
dur=max((tsec[-1]-tsec[0])/3600.0, 1e-9); scale=target_h/dur

# SoC
e_batt_inc=-e_dc
soc=np.empty_like(tsec); soc[0]=soc0
for i in range(1,len(soc)): soc[i]=soc[i-1]+(e_batt_inc[i]/cap)*100.0
if clip_soc: soc=np.clip(soc,0.0,100.0)

cum_ac=np.cumsum(e_ac); cum_dc=np.cumsum(e_dc)
td=pd.to_timedelta(tsec-tsec[0], unit='s')

# Charts
f1=go.Figure(); f1.add_trace(go.Scatter(x=td,y=dfc['freq'],mode='lines',name='Freq')); f1.add_hline(y=f_ctr,line=dict(dash='dot'),annotation_text='中心')
f1.update_layout(title='周波数', xaxis_title='時間', yaxis_title='Hz', hovermode='x unified')
f2=go.Figure(); f2.add_trace(go.Scatter(x=td,y=cmd,mode='lines',name='出力指令[%]')); f2.add_hline(y=0,line=dict(dash='dash'))
f2.update_layout(title='BESS 出力指令 [%]', xaxis_title='時間', yaxis_title='%', hovermode='x unified')
f3=go.Figure(); f3.add_trace(go.Scatter(x=td,y=p_ac,mode='lines',name='AC出力[kW]')); f3.add_trace(go.Scatter(x=td,y=p_dc,mode='lines',name='DC出力[kW]')); f3.add_hline(y=0,line=dict(dash='dash'))
f3.update_layout(title='BESS 出力（AC/DC）', xaxis_title='時間', yaxis_title='kW', hovermode='x unified')
f4=go.Figure(); f4.add_trace(go.Scatter(x=td,y=soc,mode='lines',name='SoC[%]')); f4.add_hline(y=0,line=dict(dash='dot')); f4.add_hline(y=100,line=dict(dash='dot'))
f4.update_layout(title='SoC の推移', xaxis_title='時間', yaxis_title='SoC [%]', hovermode='x unified')
st.plotly_chart(f1, use_container_width=True); st.plotly_chart(f2, use_container_width=True); st.plotly_chart(f3, use_container_width=True); st.plotly_chart(f4, use_container_width=True)
with st.expander('AC/DC 累積エネルギー（サイン付き, 参考）'):
    fc=go.Figure(); fc.add_trace(go.Scatter(x=td,y=cum_ac,mode='lines',name='累積 AC[kWh]')); fc.add_trace(go.Scatter(x=td,y=cum_dc,mode='lines',name='累積 DC[kWh]')); fc.add_hline(y=0,line=dict(dash='dash'))
    fc.update_layout(title='累積エネルギー（AC/DC, 符号付き）', xaxis_title='時間', yaxis_title='kWh', hovermode='x unified'); st.plotly_chart(fc, use_container_width=True)
st.subheader('エネルギー指標（AC端・DC端）')
c1,c2,c3=st.columns(3); c1.metric('AC 輸出（区間）', f'{export_ac:,.2f} kWh'); c2.metric('AC 輸入（区間）', f'{import_ac:,.2f} kWh'); c3.metric('期間', f'{dur:.2f} h')
c4,c5,c6,c7=st.columns(4); c4.metric(f'AC 輸出（換算 {target_h:.0f}h）', f'{export_ac*scale:,.2f} kWh/{target_h:.0f}h'); c5.metric(f'AC 輸入（換算 {target_h:.0f}h）', f'{import_ac*scale:,.2f} kWh/{target_h:.0f}h'); c6.metric('DC 放電（区間）', f'{dis_dc:,.2f} kWh'); c7.metric('DC 充電（区間）', f'{chg_dc:,.2f} kWh')
c8,c9=st.columns(2); c8.metric(f'DC 放電（換算 {target_h:.0f}h）', f'{dis_dc*scale:,.2f} kWh/{target_h:.0f}h'); c9.metric(f'DC 充電（換算 {target_h:.0f}h）', f'{chg_dc*scale:,.2f} kWh/{target_h:.0f}h')
buf=io.StringIO()
pd.DataFrame({'time[s]':tsec,'freq[Hz]':dfc['freq'],'cmd_percent[%]':cmd,'p_ac[kW]':p_ac,'p_dc[kW]':p_dc,'e_inc_ac[kWh]':e_ac,'e_inc_dc[kWh]':e_dc,'soc[%]':soc,'cum_ac[kWh]':cum_ac,'cum_dc[kWh]':cum_dc}).to_csv(buf,index=False)
st.download_button('CSVダウンロード（AC/DC・SoC・累積含む）', data=buf.getvalue().encode('utf-8'), file_name='bess_acdc_soc_with_ac_results.csv', mime='text/csv')
