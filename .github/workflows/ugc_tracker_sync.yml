name: UGC Tracker Sync

# Deliberately its own workflow, its own concurrency group, its own
# schedule slot — entirely separate from the email outreach pipeline's
# "google-sheets-api" group, so this can never queue behind (or be
# queued behind by) auto-send, check-replies, dashboard, or the Asana
# sync those already use. A failure or slowdown here cannot affect
# that pipeline, and vice versa.
on:
  schedule:
    - cron: "*/30 * * * *"  # every 30 minutes — change to "0 * * * *" for hourly if preferred
  workflow_dispatch: {}

concurrency:
  group: ugc-tracker-sync
  cancel-in-progress: false

jobs:
  sync:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          pip install --break-system-packages \
            gspread==6.1.2 google-auth==2.34.0 requests>=2.31

      - name: Run sync
        env:
          GOOGLE_SERVICE_ACCOUNT_JSON: ${{ secrets.GOOGLE_SERVICE_ACCOUNT_JSON }}
          # Reuses the SAME Asana token the Creator Outreach Asana sync
          # already uses elsewhere in this repo (ASANA_ACCESS_TOKEN) —
          # the script's own env var is still named ASANA_TOKEN
          # internally, mapped from that existing secret here rather
          # than adding a second Asana secret.
          ASANA_TOKEN: ${{ secrets.ASANA_ACCESS_TOKEN }}
          # Every one of these identifies a real, private company
          # resource (a specific Sheet, specific Drive folders, a
          # specific Asana project) — this repo is public, so none of
          # these are ever hardcoded in the script or written as a
          # literal argument here. Add all four under this repo's
          # Settings → Secrets and variables → Actions before the
          # first run:
          UGC_TRACKER_SHEET_ID: ${{ secrets.UGC_TRACKER_SHEET_ID }}
          UGC_TRACKER_RAW_FOLDER_ID: ${{ secrets.UGC_TRACKER_RAW_FOLDER_ID }}
          UGC_TRACKER_TIKTOK_FOLDER_ID: ${{ secrets.UGC_TRACKER_TIKTOK_FOLDER_ID }}
          # If other scripts in this repo already hardcode the Creator
          # Outreach project's GID, set this secret to that same value
          # rather than changing anything else.
          UGC_TRACKER_ASANA_PROJECT_GID: ${{ secrets.UGC_TRACKER_ASANA_PROJECT_GID }}
        run: |
          set -o pipefail
          python ugc_tracker_sync.py --worksheet-name Tracker | tee ugc_sync_output.txt

      - name: Add to job summary
        if: always()
        run: |
          echo '```' >> "$GITHUB_STEP_SUMMARY"
          cat ugc_sync_output.txt >> "$GITHUB_STEP_SUMMARY"
          echo '```' >> "$GITHUB_STEP_SUMMARY"
