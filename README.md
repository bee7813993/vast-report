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
- `state_file`: 契約価格推定用の状態ファイル。既定は `state/machine-contract-state.json`
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

契約価格推定の状態ファイルを、既存の日次アーカイブから古い順に再構築します。

```bash
bin/analyze-vast-daily --rebuild-state
```

## 出力

`report_dir` に以下を出力します。

```text
reports/report-YYYY-MM-DD.md
reports/recommendation-YYYY-MM-DD.json
```

## Earnings集計

machine別Earningsは、次の優先順位で集計します。

1. `earnings-last24h.json` またはmachine別Earnings JSONの `per_machine` に一致する `machine_id` があれば、それを使います。
2. 一致する `per_machine` がない場合のみ、machine別Earnings JSONの `summary.total_gpu` / `summary.total_stor` / `summary.total_bwu` / `summary.total_bwd` を使います。
3. 後方互換のfallbackとして `earnings-last24h-summary.tsv` の `scope=machine` を使います。

`--machine_id` 指定時の `per_day` はhost全体の値になり得るため、machine別集計には使いません。
host全体のEarnings集計は、従来どおり全体取得の `per_day` を使います。Vast API形式の `per_day.total_earn` はGPU収益として扱い、ストレージ・帯域収益を加えた総収益と分けて、`host_gpu_earn_per_hour` / `host_total_earn_per_hour` を出力します。
Earningsレスポンス内の `reliability` は価格判断に使わず、価格判断用のReliabilityは `reliability-last24h.tsv` を使います。

アーカイブ内のmachine別Earnings JSONは、`earnings-last24h-machine-<machine_id>.json`、`earnings-last24h-<machine_id>.json`、`machine-<machine_id>-earnings-last24h.json` の名前を認識します。

### machine別Earnings収集補助

既存の収集スクリプト内で日次アーカイブ用の作業ディレクトリを作ったあと、tar化する前に次を実行すると、解析CLIが認識できるmachine別Earnings JSONを作れます。

```bash
cd /home/bee/vast-report
bin/collect-machine-earnings --output-dir "$WORK_DIR" --machine-id 28351 --machine-id 35058
```

`--machine-id` を省略した場合は `config.yaml` の `machines` から対象IDを読みます。

```bash
bin/collect-machine-earnings --output-dir "$WORK_DIR" --config /home/bee/vast-report/config.yaml
```

この補助CLIは各マシンについて `vastai show earnings --machine_id <machine_id>` を実行し、`earnings-last24h-machine-<machine_id>.json` をatomic writeします。Vast.ai CLIのstdoutはメモリ上でJSON解析するだけで、`per_day` や未知のrawフィールドは保存しません。保存するのは `per_machine` の一致行、または一致行がない場合の `summary.total_gpu` / `summary.total_stor` / `summary.total_bwu` / `summary.total_bwd` など、集計に必要なキーだけです。

git管理版の収集スクリプト `bin/collect-vast-daily` には、このmachine別Earnings収集が組み込み済みです。cronから使う場合は、既存の `/home/bee/bin/collect-vast-daily.sh` を直接編集する代わりに、cronの実行先を `/home/bee/vast-report/bin/collect-vast-daily` へ切り替える運用にすると、収集処理もgitで追跡できます。

```cron
15 9 * * * bee bash /home/bee/vast-report/bin/collect-vast-daily
```

`VASTAIPATH`、`BASE_DIR`、`MACHINE_EARNINGS_MACHINE_IDS`、`MACHINE_EARNINGS_COLLECTOR` などは環境変数で上書きできます。既定では `MACHINE_EARNINGS_MACHINE_IDS="28351 35058"` を収集します。

Markdownレポートには、ホスト全体のGPU収益/hと総収益/h、結論、直近24時間実績、市場状況、候補価格の競合順位、Warnings、メモを出します。直近24時間実績では、現在のListed価格と、状態ファイルから推定した契約価格を分けて表示します。

JSON推奨設定には、マシンごとの現在価格、推奨価格、判断、理由、稼働率、収益/h、候補価格順位を出します。

判断は `hold`、`consider_raise`、`consider_raise_soft`、`watch_lower`、`lower`、`unknown` のいずれかです。`consider_raise_soft` は稼働率100%、空き時間0、Reliabilityが通常閾値の近傍、かつ1段上または2段上の候補価格でも競合順位が悪化しない場合の弱い値上げ提案です。

値上げ・値下げ注意の実績評価では、現在のListed価格ではなく、可能な限り `active_contract_price_estimate` を使います。次に設定すべき価格や候補価格順位はListed価格と候補価格を基準に扱います。

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
- `state/` は実行時に生成されるためgit管理しません。

## 主なファイル

```text
bin/analyze-vast-daily        CLI入口
bin/collect-vast-daily        日次データ収集スクリプト
bin/collect-machine-earnings  machine別Earnings収集補助
bin/send-vast-report          最新Markdownを標準出力へ出す
config.yaml                   マシンID、価格、候補価格、閾値
vast_report/archive.py        .txz探索・安全展開
vast_report/collect_machine_earnings.py machine別Earnings収集補助
vast_report/config.py         設定読み込み
vast_report/contract_state.py 契約価格推定stateの更新
vast_report/loaders.py        TSV/JSON読み込み
vast_report/metrics.py        稼働率・市場・収益・順位計算
vast_report/recommendations.py ルールベース推奨
vast_report/render.py         Markdown/JSON整形
```
