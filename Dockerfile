# FitPath public demo (Hugging Face Spaces, Docker SDK): dashboard + API on port 7860.
# The H&M-derived data is not in the image: space/start.sh downloads it at startup
# from a private Hugging Face dataset (FITPATH_DATA_REPO, read with the HF_TOKEN secret).

FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
RUN useradd -m -u 1000 user
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt huggingface_hub
COPY --chown=user backend/ backend/
COPY --chown=user space/ space/
COPY --from=web --chown=user /web/dist frontend/dist
RUN mkdir -p data && chown user data
USER user
ENV PYTHONPATH=/app/backend PYTHONUNBUFFERED=1 FITPATH_DATA_REPO=andrea-zendata/fitpath-data FITPATH_DEFAULT_RUN=run-20261017-d0ae33
EXPOSE 7860
CMD ["sh", "space/start.sh"]
