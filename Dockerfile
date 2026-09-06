FROM python:3.12.7-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN apt-get update && apt-get install -y gcc build-essential && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
COPY . /app
EXPOSE 8024
# Schema lives in config/report_mapping.py (see README) -- mount a corrected
# copy over /app/config/report_mapping.py for an offline deployment whose
# ClickHouse schema differs; no rebuild required.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8024"]
