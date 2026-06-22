# vast-report

Vast.ai GPUホストの日次アーカイブを解析し、価格設定の判断材料になるMarkdownレポートとJSON推奨設定を生成するCLIです。

このリポジトリは価格変更を自動実行しません。Vast.ai APIキーや認証情報も出力しません。

## セットアップ

```bash
cd /home/bee/vast-report
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## 設定

`config.yaml` に以下を定義します。

- `archive_dir`: 日次アーカイブ置き場。既定は `/home/bee/vast-daily`
- `report_dir`: レポート出力先。既定は `reports`
- `machines`: machine ID、GPU名、現在価格、候補価格、閾値
- `strategy.observation_minutes`: `machine-status-last24h.tsv` の観測間隔
- `strategy.auto_apply_price_change`: 常に `false` を想定。CLIは値が `true` でも価格変更しません

## 実行方法

最新アーカイブを解析します。

```bash
bin/analyze-vast-daily --latest
```

特定アーカイブを解析します。

```bash
bin/analyze-vast-daily --archive /home/bee/vast-daily/vast-daily-2026-06-22.txz
```

設定ファイルを明示する場合:

```bash
bin/analyze-vast-daily --latest --config config.yaml
```

## 出力

`report_dir` に以下を出力します。

```text
reports/report-YYYY-MM-DD.md
reports/recommendation-YYYY-MM-DD.json
```

Markdownレポートには、結論、直近24時間実績、市場状況、候補価格の競合順位、Warnings、メモを出します。

JSON推奨設定には、マシンごとの現在価格、推奨価格、判断、理由、稼働率、収益/h、候補価格順位を出します。

## 最新レポート表示

初版の送信スクリプトは送信せず、最新Markdownを標準出力に出すだけです。

```bash
bin/send-vast-report --latest
```

## 運用上の注意

- Vast.aiの価格変更コマンドは実行しません。
- `/etc/cron.d`、`/var/log`、`/home/bee/bin` はこのリポジトリの作業対象外です。
- root権限が必要な操作はしません。
- `earnings-last24h.raw.json` は扱いません。アーカイブ内にあっても無視します。
- `.txz` は一時ディレクトリへ安全に展開し、解析後に後片付けします。
- TSV/JSONが欠損または空の場合も、可能な限り `-` と warning を出してレポート生成を続行します。

## 主なファイル

```text
bin/analyze-vast-daily        CLI入口
bin/send-vast-report          最新Markdownを標準出力へ出す
config.yaml                   マシンID、価格、候補価格、閾値
vast_report/archive.py        .txz探索・安全展開
vast_report/config.py         設定読み込み
vast_report/loaders.py        TSV/JSON読み込み
vast_report/metrics.py        稼働率・市場・収益・順位計算
vast_report/recommendations.py ルールベース推奨
vast_report/render.py         Markdown/JSON整形
```
