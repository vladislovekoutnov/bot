FROM python:3.9-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

# Открываем порт, на котором будет слушать приложение
ENV PORT 8080

# Запуск Uvicorn для ASGI-приложения
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
