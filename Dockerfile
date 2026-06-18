FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY megaphone_server.py .
COPY megaphone/ ./megaphone/

EXPOSE 8080

CMD ["python", "megaphone_server.py"]
