# 周波数可視化アプリ（中心周波数・偏差対応版）

- Excel/CSVから読み込み、周波数の時系列と **中心周波数からの偏差（Δf）** を表示します。
- Δfは **Hz / mHz** で切り替え可能。中心周波数は **平均からの自動設定** または **手動入力**。
- ダウンロードは **CSVのみ**（Δf列付き）。

## ローカル実行
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```
