FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

ENV DATABASE_PATH=/data/smashcord.sqlite

VOLUME ["/data"]

CMD ["python", "app.py"]
