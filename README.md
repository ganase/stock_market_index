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


## Tableau export / publish

このリポジトリは既存の市場データ更新後に、Tableau で扱いやすい 1 ファイルの正規化 CSV を生成できます。

### 生成されるファイル

- `exports/tableau/fact_market_prices_daily.csv`
  - 個別銘柄の日次ファクトテーブル
  - 主な列: `trade_date`, `symbol`, `ticker_local`, `company_name`, `sector`, `open`, `high`, `low`, `close`, `volume`, `source`, `source_file`, `fetched_at`
- `exports/tableau/fact_market_indexes_daily.csv`
  - 指数の日次ファクトテーブル
  - 主な列: `trade_date`, `index_name`, `open`, `high`, `low`, `close`, `source`, `source_file`, `fetched_at`

### 入力元

- 価格: `data/prices/**/**/*.csv`
- 指数: `data/indexes/**/**/*.csv`
- メタデータ補完: `data/constituents/**/**/*.csv`, `data/panels/*.csv`

現在の実装では、Nikkei 225 / JPX を対象に次の固定値で出力します。

- `market_code = JPX`
- `universe_code = NIKKEI225`
- `index_name = Nikkei 225`
- `currency = JPY`

### 使い方

ローカル生成のみ:

```bash
python scripts/build_and_publish_tableau_feed.py --repo-root .
```

Google Drive へ公開:

```bash
python scripts/build_and_publish_tableau_feed.py --repo-root . --publish-destination google-drive
```

### Google Drive 用の環境変数

- `GOOGLE_DRIVE_FOLDER_ID`
  - アップロード先 Google Drive フォルダ ID
- `GOOGLE_SERVICE_ACCOUNT_JSON_BASE64`
  - サービスアカウント JSON 全体を base64 エンコードした文字列

`--publish-destination google-drive` を指定した場合だけ、これらの環境変数が必須です。未設定の場合は明確なエラーで終了します。`local` では不要です。既存ファイル名が同じ場合は Google Drive 上で更新し、重複作成を避けます。サービスアカウントを使う場合は、保存先を個人の「マイドライブ」ではなく Shared Drive 配下のフォルダにし、その Shared Drive / フォルダをサービスアカウントへ共有してください。

### GitHub Actions での動作

日次 workflow はまず既存の index / price 更新を実行し、その後で Tableau 向け CSV を生成します。

- デフォルトでは `local` 出力のみ
- `workflow_dispatch` で `publish_destination=google-drive` を指定し、必要な secrets がある場合だけ Google Drive へ公開
- secrets がない通常実行では、既存の市場データ更新フローを壊さずにローカル生成だけ継続

### Tableau での使い方

- `fact_market_prices_daily.csv` を個別銘柄の明細テーブルとして接続
- `fact_market_indexes_daily.csv` を指数テーブルとして接続
- `trade_date` を日付ディメンションとして利用し、`symbol` や `sector` でフィルタや色分けを設定
- `source_file` を保持しているため、必要なら元データファイル粒度まで追跡できます

### 構成銘柄データに関する注意

- 現在の構成銘柄は `data/constituents/nikkei225/current.csv` の固定ファイルを使用しており、ライブスクレイピングには戻していません
- 価格データの `trade_date` と、構成銘柄 CSV の `as_of_date` は一致しない場合があります
- そのため、エクスポート上の会社名やセクターは「現在の固定 constituents ファイルに基づくメタデータ補完」です
