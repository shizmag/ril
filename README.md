# Read It Later (RIL)

> «Я устал от бесконечных открытых вкладок и сервисов вроде Pocket, которые превращаются в кладбище ссылок, куда я никогда не возвращаюсь. Мне нужен был простой, локальный склад, куда можно быстро скинуть статью и потом легко работать с ней через ИИ».

**RIL** — это легковесный персональный архиватор веб-страниц, созданный для решения проблемы информационного перегруза. Вместо тяжеловесных облачных платформ с кучей рекламы и трекеров, RIL сохраняет чистые статьи в локальную папку в формате Markdown с кэшированием всех картинок. 

Вы отправляете ссылку в Telegram прямо с телефона ➡️ она моментально сохраняется в вашу базу (идеально для Obsidian/Logseq) ➡️ ваш локальный ИИ-агент (через MCP) может искать, читать и анализировать эти статьи.

---

## 💡 Почему это удобно?

*   **Без «мусора»**: Никаких баннеров, меню навигации, поп-апов и согласий на cookies. Алгоритм Readability вырезает всё лишнее, оставляя чистый текст.
*   **Вечный архив**: Все картинки скачиваются локально. Даже если сайт первоисточника закроется, в вашем Obsidian-архиве статья останется читаемой и полной.
*   **Интеграция с ИИ (MCP)**: AI-ассистент (например, Claude Desktop) может напрямую искать по вашей базе статей через SQLite FTS5 и считывать их контент, помогая делать сводки или искать инсайты (локальный RAG).
*   **Удобно на ходу (Telegram)**: Нашли крутую статью со смартфона в метро? Просто перешлите ссылку боту. Он пришлет сводку («Сохранил! В статье 1200 слов, картинки скачаны»).

---

## 🛠 Архитектура системы

```text
[ Ссылка с телефона ] ──> [ Telegram Bot ] ──┐
                                             v
[ Ссылка от агента   ] ──> [ MCP Server  ] ──┼─> [ Playwright (Stealth) ]
                                             │            │ (Рендеринг JS & обход защит)
                                             │            v
                                             │      [ Readability ]
                                             │            │ (Очистка от мусора)
                                             │            v
                                             │      [ Converter ] ──> [ Скачивание картинок ]
                                             │            │
                                             v            v
                                     [( SQLite FTS5 )]   [ Папка library/ (Markdown) ]
```

---

## 🚀 Быстрый старт

### 1. Установка зависимостей
Требуется Python `>= 3.12`.

```bash
# Клонируйте репозиторий и создайте окружение
git clone <url-репозитория>
cd ril
uv venv --python 3.12
source .venv/bin/activate

# Установите библиотеки и браузер
uv pip install -e .
.venv/bin/playwright install chromium
```

### 2. Настройка (`.env`)
Создайте файл `.env` на основе примера [.env.example](file:///Users/vladimirkasterin/python/ril/.env.example):
```ini
TELEGRAM_TOKEN=ваш_токен_от_BotFather
ALLOWED_TELEGRAM_USERS=ваш_telegram_id_для_безопасности
```

---

## 📱 Использование

### Способ 1: С телефона через Telegram-бот
Запустите бота:
```bash
.venv/bin/python main.py bot
```
Отправьте боту ссылку.
*   **Ответ бота**: `📥 Сохранил! В статье "Название" 1420 слов, картинки на месте.`
*   **Команды бота**:
    *   `/stats` — статистика прочтенного и утилизация вашего «бэклога» на русском языке.
    *   `/list` — показать последние 10 сохраненных ссылок.
    *   `/search <запрос>` — полнотекстовый поиск по всей базе.

### Способ 2: Через CLI (терминал)
```bash
# Добавить статью
.venv/bin/python main.py add "https://habr.com/ru/articles/700000/"

# Быстрый поиск в консоли
.venv/bin/python main.py search "квантовые процессоры"

# Статистика чтения
.venv/bin/python main.py stats
```

### Способ 3: Интеграция с Claude Desktop (MCP)
Добавьте этот сервер в настройки Claude (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "read-it-later": {
      "command": "/Users/vladimirkasterin/python/ril/.venv/bin/python",
      "args": [
        "/Users/vladimirkasterin/python/ril/main.py",
        "mcp"
      ],
      "env": {
        "RIL_LIBRARY_DIR": "/Users/vladimirkasterin/python/ril/library",
        "RIL_DB_PATH": "/Users/vladimirkasterin/python/ril/library/metadata.db"
      }
    }
  }
}
```
После перезапуска Claude Desktop агент сможет выполнять поиск, сохранять входящие ссылки из чата и зачитывать статьи прямо во время диалога.

---

## 📂 Структура проекта
*   [main.py](file:///Users/vladimirkasterin/python/ril/main.py) — CLI-маршрутизатор команд.
*   [ril/config.py](file:///Users/vladimirkasterin/python/ril/ril/config.py) — Переменные окружения и пути к хранилищам.
*   [ril/crawler.py](file:///Users/vladimirkasterin/python/ril/ril/crawler.py) — Скрапер на базе Playwright Stealth.
*   [ril/readability_utils.py](file:///Users/vladimirkasterin/python/ril/ril/readability_utils.py) — Обрезка лишней верстки.
*   [ril/converters.py](file:///Users/vladimirkasterin/python/ril/ril/converters.py) — Преобразование в Markdown и локальный кэш картинок.
*   [ril/db.py](file:///Users/vladimirkasterin/python/ril/ril/db.py) — Взаимодействие с SQLite и виртуальная таблица поиска FTS5.
*   [ril/telegram_bot.py](file:///Users/vladimirkasterin/python/ril/ril/telegram_bot.py) — Телеграм-бот с командами.
*   [ril/mcp_server.py](file:///Users/vladimirkasterin/python/ril/ril/mcp_server.py) — Интеграция FastMCP инструментов.
