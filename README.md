# 周波数×BESS応答（kWh集計）アプリ

- BESS定格出力 [kW] を指定して、出力指令[%]を **出力[kW]** へ換算します。
- 積分により **総放電量/総充電量 [kWh]** を算出し、**1日換算（24h）** も表示します。
- time列の単位（秒/分/時間）をサイドバーで設定してください。

## ローカル実行
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```
