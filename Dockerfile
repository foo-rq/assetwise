FROM python:3.14-slim
WORKDIR /app

# Copy dependency lists first so source edits reuse the installed package layer
COPY requirements.txt requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy only runtime files, excluding local context, credentials and browser test data
COPY backend ./backend
COPY prototype ./prototype
RUN useradd --create-home assetwise
RUN install -d -o assetwise -g assetwise /app/.assetwise
USER assetwise
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
# Mount this directory on persistent storage before relying on saves in a hosted container
ENV ASSETWISE_DB_PATH=/app/.assetwise/portfolios.sqlite3
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "20"]
