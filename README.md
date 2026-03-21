# stock_market_index 拡張パック

このパックは、既存の `stock_market_index` リポジトリを次の構成へ拡張するためのものです。

```text
.data/
  indexes/
    nikkei225/
      daily.csv
  constituents/
    nikkei225/
      current.csv
  prices/
    stooq/
      jp/
        7203.csv
        6758.csv
        ...
  panels/
    nikkei225_current_constituents_latest.csv
    nikkei225_current_constituents_close_wide_260d.csv
scripts/
  common_market_io.py
  update_nikkei225_index.py
  update_nikkei225_constituents.py
  update_nikkei225_prices.py
.github/workflows/
  update_market_data.yml
runtime/
  *.json   (git ignore)
```

## 追加手順

1. 既存 repo に `scripts/` フォルダを作る
2. このパックの `scripts/*.py` を配置する
3. `.github/workflows/update_market_data.yml` を追加する
4. ルートの `gitignore` を `.gitignore` に直し、このパックの内容に置き換える
5. 旧 workflow (`update_nikkei225.yml`) は二重実行を避けるため無効化または削除する
6. Actions の `Update market data` を `Run workflow` で1回実行する

## 生成される主要ファイル

- `data/indexes/nikkei225/daily.csv`
  - 日経平均の公式日次データをマージした正本
- `data/constituents/nikkei225/current.csv`
  - 日経公式の現在構成銘柄一覧
- `data/prices/stooq/jp/<code>.csv`
  - 現在構成銘柄ごとの日足履歴
- `data/panels/nikkei225_current_constituents_latest.csv`
  - 現在構成銘柄の最新行だけを集めた一覧
- `data/panels/nikkei225_current_constituents_close_wide_260d.csv`
  - 直近260営業日ぶんの終値ワイド表

## 補足

- 個別銘柄の価格ソースは Stooq 想定です
- 現在構成銘柄ベースなので、過去の入れ替えまで厳密に再現する設計ではありません
- 将来は `topix` や `sp500` などを同じ構成で横展開できます
