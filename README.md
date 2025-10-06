# 周波数可視化アプリ（Streamlit）

このアプリは Excel/CSV から周波数の時系列を読み込み、インタラクティブに可視化します。

## 使い方（ローカル）
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## デプロイ（Streamlit Cloud）
1. このリポジトリを GitHub にアップロード。
2. https://streamlit.io/cloud で GitHub 連携し、対象リポジトリをデプロイ。

## 入力フォーマット
- Excel: `.xlsx`, `.xls`（複数シート対応）
- CSV: UTF-8想定
- 列名の候補（自動推定）: 時間列: `time`, `時間`, `時刻`, `秒`, `sec`, `s` / 周波数列: `freq`, `周波数`, `frequency`, `hz`

## ライセンス
MIT
