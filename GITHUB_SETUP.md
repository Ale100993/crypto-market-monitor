# GitHub Actions deployment

1. Upload all project files including `.github/workflows/market-monitor.yml` to the repository main branch. The `.github` directory must remain nested as shown.
2. Go to repository Settings > Actions > General > Workflow permissions and allow **Read and write permissions** if your repository permits it. Workflow has `contents: write`.
3. Go to Actions > Crypto Market Monitor > Run workflow > Run workflow.
4. Wait for the green run; check output/latest.json and output/latest.html in repository.
5. Public raw JSON URL: https://raw.githubusercontent.com/Ale100993/crypto-market-monitor/main/output/latest.json

GitHub scheduled workflows may run late or be dropped. The cron times target 5 minutes before China open and US open/close, but this is not a guaranteed precise scheduler. The DST gate prevents duplicate USA runs on both UTC candidates. Holidays are not filtered. ChatGPT scheduled tasks are not yet connected to this URL: test direct retrieval before changing their prompts.

No API secrets are used. Only latest JSON/HTML/CSV are committed; SQLite and logs remain local to each run.
