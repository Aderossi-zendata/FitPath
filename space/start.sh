#!/bin/sh
# Startup for the Hugging Face Space: fetch the prepared data, then serve dashboard + API.
set -e
cd /app
if [ -n "$FITPATH_DATA_REPO" ] && [ ! -f data/parquet/transactions.parquet ]; then
  echo "Downloading data from $FITPATH_DATA_REPO ..."
  python - <<'PY'
import os
from huggingface_hub import snapshot_download
snapshot_download(repo_id=os.environ["FITPATH_DATA_REPO"], repo_type="dataset", local_dir="data",
                  token=os.environ.get("HF_TOKEN"))
PY
fi
# the dashboard opens on the newest run: make it the Black Friday 2019 one the pitch is built on
if [ -n "$FITPATH_DEFAULT_RUN" ] && [ -f "data/out/sim/$FITPATH_DEFAULT_RUN/summary.json" ]; then
  touch "data/out/sim/$FITPATH_DEFAULT_RUN/summary.json"
fi
cd backend
exec uvicorn fitpath.serve:app --host 0.0.0.0 --port "${PORT:-7860}"
