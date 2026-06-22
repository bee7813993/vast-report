# vast-report

Vast.ai daily archive analyzer.

## Usage

```bash
cd /home/bee/vast-report
. .venv/bin/activate
bin/analyze-vast-daily --latest
cat reports/report-YYYY-MM-DD.md
Files
config.yaml: machine IDs, current prices, candidate prices, strategy thresholds
bin/analyze-vast-daily: analyze latest or specified daily archive
bin/send-vast-report: output latest generated report
