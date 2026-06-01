# One-time setup checklist

Follow this in order. Steps 1–4 are Google Cloud; steps 5–7 are GitHub.

## 1. Create a GCP project

1. Go to <https://console.cloud.google.com>.
2. Top-left project picker → **New project** → name it (e.g. `nsw-pharmacy-scraper`) → **Create**.
3. Wait ~10 seconds; project picker should now show the new project as selected.

## 2. Enable APIs

In the new project:

1. <https://console.cloud.google.com/apis/library/sheets.googleapis.com> → **Enable**.
2. <https://console.cloud.google.com/apis/library/drive.googleapis.com> → **Enable**.

## 3. Create a service account + download JSON key

1. <https://console.cloud.google.com/iam-admin/serviceaccounts> → **Create service account**.
2. Name: `pharmacy-scraper`. Leave roles empty. Click **Done**.
3. From the list, click the new service account → **Keys** tab → **Add key** → **Create new key** → **JSON** → **Create**.
4. A JSON file downloads. Keep it safe; do **not** commit it. Note the `client_email` inside (looks like `pharmacy-scraper@<project>.iam.gserviceaccount.com`).

## 4. Share the target Google Sheet with the service account

1. Open <https://docs.google.com/spreadsheets/d/1502YpdciO9NSeyzBEr6wf75Y5Gpan-qPXw0elKHve0c>.
2. Top-right **Share** button.
3. Paste the `client_email` from step 3. Permission: **Editor**. Uncheck **Notify people**. Click **Share**.

## 5. Push this code to a new GitHub repo

1. Create an empty repo at <https://github.com/new> (private or public — your call).
2. In a terminal from the project directory:

```powershell
git init
git add .
git commit -m "feat: initial scaffold"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

## 6. Add GitHub Actions secrets

In the repo on GitHub: **Settings → Secrets and variables → Actions → New repository secret**.

1. Name: `GOOGLE_SERVICE_ACCOUNT_JSON`. Value: paste the **entire contents** of the JSON file from step 3 (open it in a text editor and copy everything — including the curly braces).
2. (Optional) Name: `SHEET_ID`. Value: `1502YpdciO9NSeyzBEr6wf75Y5Gpan-qPXw0elKHve0c`. Skip if you're happy with the default in `src/main.py`.

## 7. Trigger the first run manually

1. **Actions** tab → **Weekly NSW Pharmacy Scrape** (left sidebar) → **Run workflow** → **Run workflow** (green button).
2. Wait ~30–50 minutes for completion. Watch progress live by clicking into the running job.
3. When done, open the sheet. Three tabs should be populated:
   - **Current** — every NSW pharmacy with status `Current` (~2000+ rows)
   - **Changes** — every row appears here as `added` on the first run (expected — there was no previous snapshot to compare against). From week 2 onward this will only show real diffs.
   - **Runs** — a single row with `status: success`.

## After setup

The cron schedule (Mondays 02:00 UTC = ~12:00 noon Sydney time depending on DST) runs automatically. To trigger a manual run, repeat step 7.

The `snapshot.json` artifact is attached to each workflow run for 90 days — find it under the run's **Artifacts** section if you want to inspect the raw data outside the sheet.
