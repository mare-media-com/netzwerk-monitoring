FROM python:3.11-slim

WORKDIR /app

# System-Tools
RUN apt-get update && apt-get install -y \
    iputils-ping \
    curl \
    && rm -rf /var/lib/apt/lists/*

# requirements zuerst kopieren (Caching!)
COPY requirements.txt .

# Python-Pakete installieren
RUN pip install --no-cache-dir -r requirements.txt

# Restliche App wird per Volume gemountet

EXPOSE 5000

CMD ["python3", "server.py"]
