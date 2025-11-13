FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Create data directory
RUN mkdir -p /data

EXPOSE 8080

CMD ["python", "app.py"]
