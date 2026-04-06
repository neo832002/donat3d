# Используем официальный образ Python с поддержкой Debian
FROM python:3.14-slim

# Устанавливаем необходимые системные зависимости и Rust toolchain
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && curl https://sh.rustup.rs -sSf | sh -s -- -y \
    && . $HOME/.cargo/env \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Добавляем Rust в PATH
ENV PATH="/root/.cargo/bin:${PATH}"

# Создаем рабочую директорию
WORKDIR /app

# Копируем файлы проекта в контейнер
COPY . /app

# Обновляем pip, setuptools, wheel
RUN pip install --upgrade pip setuptools wheel

# Устанавливаем зависимости из requirements.txt
RUN pip install -r requirements.txt

# Указываем переменную окружения для токена (можно переопределить при запуске)
ENV TELEGRAM_TOKEN="8527322806:AAE570ZADxH89_9bDyNWO2JZ9WqEYJvjvJQ"

# Запускаем бота
CMD ["python", "bot.py"]
