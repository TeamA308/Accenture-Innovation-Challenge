# Alternative to `python run.py`. The supported path is the run script; this is
# here for teams that prefer containers. Single image, no database server: the
# app uses SQLite unless DATABASE_URL points elsewhere.
FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend/ /app/backend/
COPY seed/ /app/seed/
COPY frontend/dist/ /app/frontend/dist/

ENV PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000
WORKDIR /app/backend
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
