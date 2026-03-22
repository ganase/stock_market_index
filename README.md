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


## 現在の運用状態と毎日の更新について

### 毎日自動で更新されるか

はい。現在の workflow は GitHub Actions の schedule で **毎週 月曜〜金曜の 07:45 UTC（16:45 JST）** に自動実行されます。

現在の定期実行では、次の順番で処理されます。

1. `data/indexes/nikkei225/daily.csv` を更新
2. `data/prices/stooq/jp/*.csv` と `data/panels/*.csv` を更新
3. Tableau 用 CSV を生成
4. Google Drive secrets が設定済みなら Google Drive に publish
5. `data/` 配下の変更があれば GitHub に commit / push

つまり、**いまの設定で平日ごとに市場データ更新 + Tableau 出力 + Google Drive publish が自動実行される状態**です。

### 手動実行との違い

- `workflow_dispatch` で手動実行する場合は `publish_destination` を選べます
- `local` を選ぶとローカル export のみ
- `google-drive` を選ぶと Google Drive publish まで実行
- 定期実行 (`schedule`) は、Google Drive secrets が入っていれば自動的に Google Drive publish まで進みます

## 今回設定した内容の完全メモ

以下は、このリポジトリで今回有効化した内容の**完全チェックリスト**です。今後の再設定や引き継ぎのために、この順番で見れば漏れません。

### A. GitHub リポジトリ側で設定したもの

#### 1. Workflow

- ファイル: `.github/workflows/update_market_data.yml`
- 役割:
  - 平日 07:45 UTC / 16:45 JST に自動実行
  - 手動実行も可能
  - index 更新 → price 更新 → Tableau export → Google Drive publish の順に実行

#### 2. Repository secrets

GitHub の **Settings → Secrets and variables → Actions** で次を登録します。

- `GOOGLE_DRIVE_FOLDER_ID`
  - Google Drive の保存先フォルダ ID
- `GOOGLE_SERVICE_ACCOUNT_JSON_BASE64`
  - サービスアカウント JSON 全体を base64 化した文字列

#### 3. 無視対象

- `.gitignore` に `exports/tableau/*.csv` を追加
- 生成された Tableau CSV は git commit しない

### B. Google Cloud / Google Drive 側で設定したもの

#### 1. Google Cloud プロジェクト

- サービスアカウントを作成
- Google Drive API を有効化

#### 2. サービスアカウント

- 使用したサービスアカウントのメールアドレス例:
  - `stock-market-index@stock-market-index.iam.gserviceaccount.com`
- このサービスアカウントの JSON キーを発行
- JSON 全体を base64 化して GitHub secret `GOOGLE_SERVICE_ACCOUNT_JSON_BASE64` に登録

#### 3. Shared Drive

- 保存先は **個人のマイドライブではなく Shared Drive 配下のフォルダ** を使用
- 対象 Shared Drive または対象フォルダを、サービスアカウントのメールアドレスへ共有
- 推奨権限は、少なくともファイル作成・更新ができる権限（編集者以上）

#### 4. 保存先フォルダ ID

- Shared Drive 上の保存先フォルダ URL からフォルダ ID を取得
- その ID のみを `GOOGLE_DRIVE_FOLDER_ID` に設定
- **URL 全体は入れない**

### C. リポジトリ内で追加・変更されたファイル

#### 1. `scripts/build_and_publish_tableau_feed.py`

- Tableau 用に 2 つの 1-file CSV を生成
  - `exports/tableau/fact_market_prices_daily.csv`
  - `exports/tableau/fact_market_indexes_daily.csv`
- 必要に応じて Google Drive に upload / update

#### 2. `.github/workflows/update_market_data.yml`

- 手動実行 input `publish_destination` を追加
- schedule 実行を有効化
- Google Drive secrets を job env に注入
- 条件付きで local export / Google Drive publish を実行

#### 3. `README.md`

- Tableau export の用途
- 生成ファイル
- Google Drive の設定
- 運用方法
- 注意点
を記録

#### 4. `.gitignore`

- `exports/tableau/*.csv` を無視対象に追加

## 今後、追加でやるべきことがあるか

通常運用に入るうえで、**必須の追加作業は基本的にありません**。

ただし、運用上は次の点だけ定期的に意識すると安全です。

### 1. GitHub Actions の定期実行結果をたまに確認する

- Stooq 側の一時制限で失敗する場合があります
- エラーが連続する場合は、手動再実行か、取得間隔の見直しを検討します

### 2. Google サービスアカウントキーの管理

- キーをローテーションしたら `GOOGLE_SERVICE_ACCOUNT_JSON_BASE64` も更新する
- キーを再作成したら Shared Drive の共有先メールアドレスが変わっていないか確認する

### 3. 保存先フォルダの確認

- `GOOGLE_DRIVE_FOLDER_ID` のフォルダを削除・移動・権限変更すると publish が失敗します
- Shared Drive やフォルダ権限を変えた場合は再確認する

## トラブル時の見分け方

- `Service Accounts do not have storage quota`
  - マイドライブへ書こうとしている可能性が高い
  - Shared Drive 配下へ変更する
- `File not found`
  - `GOOGLE_DRIVE_FOLDER_ID` が違う、またはサービスアカウントに共有されていない
- `Exceeded the daily hits limit`
  - Stooq 側の取得制限。時間を空けて再実行する
