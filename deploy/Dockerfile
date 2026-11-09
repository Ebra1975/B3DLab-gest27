# Immagine unica: API + interfaccia. I test girano durante la build: se uno fallisce, l'immagine non viene creata.
FROM python:3.12-slim AS test
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
RUN python -m pytest -q

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 B3D_DB=/app/dati/b3d-fisco.sqlite B3D_XML=/app/dati/xml
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && apt-get update && apt-get install -y --no-install-recommends sqlite3 && rm -rf /var/lib/apt/lists/*
COPY --from=test /app/backend ./backend
COPY --from=test /app/regole ./regole
COPY --from=test /app/frontend ./frontend
COPY --from=test /app/deploy ./deploy
EXPOSE 8200
CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8200", "--workers", "1"]
