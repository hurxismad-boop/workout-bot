# Workout Bot v5

## Локальный запуск на Windows PowerShell

1. Установи зависимости:

```powershell
py -m pip install -r requirements.txt
```

2. Поставь токен только в терминале, не в коде:

```powershell
$env:BOT_TOKEN="твой_новый_токен_от_BotFather"
```

3. Запусти:

```powershell
py bot.py
```

Если всё хорошо, будет:

```text
Bot v5 starting...
```

## Деплой на Render 24/7

Создай GitHub репозиторий и загрузи туда файлы:

- bot.py
- requirements.txt
- .python-version

На Render создай Background Worker.

Настройки:

- Build Command: `pip install -r requirements.txt`
- Start Command: `python bot.py`

Environment Variables:

- BOT_TOKEN = твой токен от BotFather

Опционально для сохранения SQLite базы после рестартов:

- подключи Persistent Disk
- Mount Path: `/var/data`
- Environment Variable: `DATABASE_PATH=/var/data/workouts_v5.db`

Без persistent disk локальный SQLite-файл может пропасть после рестарта или редеплоя.
