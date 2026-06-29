# Google Sheet Setup — One Page

The Sales CRM lives in a Google Sheet. The scraper writes batches of 10 into
it; Sales updates `status` there. This is a one-time setup (~10 min). Do it once,
never again.

---

## 1. Create the Google Sheet (1 min)
1. Go to <https://sheets.google.com> → **Blank spreadsheet**.
2. Name it e.g. `ROSH Leads — Sales`.
3. From the URL, copy the **spreadsheet ID** (the long part between `/d/` and `/edit`):
   `https://docs.google.com/spreadsheets/d/`**`1AbC...xyz`**`/edit` → ID = `1AbC...xyz`.

## 2. Create a Google Cloud service account (5 min)
A service account is a "robot user" the app logs in as — so it can write to the Sheet.
1. Go to <https://console.cloud.google.com/> → create a project (or pick one).
2. **APIs & Services → Library** → search **Google Sheets API** → **Enable**.
3. **APIs & Services → Credentials → Create Credentials → Service account**.
   - Give it any name (e.g. `rosh-scraper`) → **Create and continue** → **Done**.
4. Click the new service account → **Keys** tab → **Add key → Create new key → JSON**.
   - A `.json` file downloads. This is the robot's password — keep it private.

## 3. Wire it into the app (2 min)
1. Move the downloaded JSON into the project as:
   `Customer Scrapper/config/service_account.json`
2. Open `config/.env` (copy from `config/.env.example` if it doesn't exist) and set:
   ```
   GSHEETS_CREDENTIALS_FILE=config/service_account.json
   GSHEETS_SPREADSHEET_ID=1AbC...xyz        # from step 1.3
   GSHEETS_WORKSHEET=CRM
   ```

## 4. Share the Sheet with the robot (1 min) — the step everyone forgets
1. Open `config/service_account.json`, find the `"client_email"` value
   (looks like `rosh-scraper@your-project.iam.gserviceaccount.com`).
2. In the Google Sheet → **Share** → paste that email → give it **Editor** → Send.
   *(If you skip this, the app gets a "permission denied" — the robot can't see the Sheet.)*

## 5. Test it
```bash
pip install -r requirements.txt      # installs gspread + google-auth
streamlit run app.py
```
In the app sidebar you should see **"✅ Google Sheet connected."** Run a small scrape,
click **Build Sales' next batch (10)**, and 10 rows should appear in the Sheet with
`wa.me` links. Done.

---

### Troubleshooting
| Symptom | Fix |
|---|---|
| "Google Sheet not configured" | `GSHEETS_*` keys missing/blank in `config/.env` |
| "Credentials file not found" | Path in `.env` doesn't match where you saved the JSON |
| "permission denied" / 403 | You didn't share the Sheet with the service-account email (step 4) |
| "API has not been enabled" | Enable **Google Sheets API** in Cloud Console (step 2.2) |

> The JSON key and `.env` are gitignored — they never get committed. Treat the JSON
> like a password; if it leaks, delete the key in Cloud Console and make a new one.
