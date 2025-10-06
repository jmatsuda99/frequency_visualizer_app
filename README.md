# 周波数可視化アプリ（Streamlit, 画像DLなし版）

- Excel/CSVから読み込み、周波数の時系列を可視化します。
- ダウンロードは **CSVのみ** です（画像のダウンロード機能は削除）。
- Streamlit Cloud で追加設定なしで動作します。

## ローカル実行
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```
