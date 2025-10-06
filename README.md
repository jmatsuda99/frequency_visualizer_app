# 周波数×BESS応答（kWh集計・換算）アプリ

- 観測区間のエネルギー（kWh）を、**任意の換算時間 [h]** に比例換算して表示します（既定24h）。
- BESS定格[kW]、Droop[%]、不感帯[mHz]、出力制限[%]を指定し、出力[kW]および積算kWhを算出。

## ローカル実行
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```
