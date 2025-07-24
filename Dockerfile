FROM python:3.9-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

# Говорим контейнеру слушать порт 8080
ENV PORT 8080
EXPOSE 8080

# Запуск ASGI-сервера
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
