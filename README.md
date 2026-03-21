# Nikkei 225 daily updater for GitHub Actions

This repository layout lets GitHub Actions fetch the official Nikkei 225 daily CSV, normalize it, and commit updated data back into the same repository.

## Files

- `update_nikkei225.py` - updater script
- `.github/workflows/update_nikkei225.yml` - scheduled GitHub Actions workflow
- `data/nikkei225_latest_3years.csv` - current official snapshot
- `data/nikkei225_master.csv` - merged long-lived local history built from repeated runs

## How to use

1. Create a new GitHub repository.
2. Upload all files from this starter pack.
3. Open the **Actions** tab and enable workflows if asked.
4. Run **Update Nikkei 225 data** once with **Run workflow**.
5. Confirm that `data/nikkei225_latest_3years.csv` and `data/nikkei225_master.csv` were created and committed.

## Notes

- The public Nikkei CSV covers the latest 3 years. Your `nikkei225_master.csv` grows over time only from the days you keep collecting.
- `data/nikkei225_status.json` is intentionally ignored so non-market days do not create empty commits.
- If your repository uses strict branch protection, direct pushes from the workflow may be blocked. In that case, switch the workflow to create a pull request instead of pushing directly.
