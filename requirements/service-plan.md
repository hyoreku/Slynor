# Окружения Music Service и Telegram

- Music Service использует FastAPI, Pydantic и PyAV из GUI-набора. Провайдеры запускаются в отдельном окружении.
- Telegram-клиент использует [bot.txt](bot.txt). Прямые зависимости закреплены.
- Очередь и локальные данные хранятся в SQLite. Для серверной схемы см. [план VPS](../docs/ROADMAP_RU.md#4-vps-в-рф).
