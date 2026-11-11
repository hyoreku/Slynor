# Будущая группа bot/service — не установка

После приёмки этапа 0 и эксперимента источников создать отдельное окружение Python 3.13 x64.
Кандидаты из ТЗ: aiogram 3.x, FastAPI, Uvicorn, Pydantic, httpx, SQLAlchemy 2.x, Alembic.
SQLite WAL — локальный persistent volume. Redis/Celery/PostgreSQL на старте не добавляются.
Точные версии, транзитивные зависимости, лицензии и wheel-хеши не проверены.
Пустой requirements, выдающий успешный pip install без сервиса, намеренно не создаётся.
