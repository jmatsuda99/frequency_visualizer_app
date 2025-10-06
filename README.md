# 周波数×BESS応答 可視化アプリ

- 調停率（Droop %）、不感帯（mHz）、上限/下限[%] を指定し、**出力指令[%]** を可視化します。
- Δfの単位は **Hz/mHz** で切替、中心周波数は平均から自動 or 手動指定。

### 出力指令の算出
```
cmd_pu = - (Δf / f_nom) / (droop_pct/100)   # per-unit
cmd_%  = clip(cmd_pu * 100, 下限%, 上限%)
```
- 既定の符号：Δf<0（周波数低下）→ 指令 +%（放電）。必要に応じて反転可。

## ローカル実行
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```
