FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /code

# UPDATED: Added tesseract-ocr, poppler-utils, and antiword
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    tesseract-ocr \
    poppler-utils \
    antiword \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /code/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /code/

# Add this line exactly here:
COPY credentials.json /app/credentials.json

# Generate static files during build
RUN python manage.py collectstatic --noinput

# Respect Cloud Run $PORT (default to 8000 locally) and serve via gunicorn
CMD ["bash", "-c", "PORT=${PORT:-8000} gunicorn hr_analyst.wsgi:application --bind 0.0.0.0:$PORT"]