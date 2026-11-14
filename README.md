# Vitalis | регистрация

Telegram-бот регистрации на встречи бизнес-клуба Vitalis: `@vitalis_business_club_bot`.
Разработка и оператор персональных данных: JBD (justbusinessdarling.com, hello@justbusinessdarling.com).

Стек: Python 3.12, aiogram 3, PostgreSQL (Yandex Managed PostgreSQL), SQLAlchemy + asyncpg, alembic,
reportlab + qrcode. Хостинг: Railway, long polling.

## Что умеет

- Карточка ближайшей встречи и именной билет как Telegram-виджеты (Bot API 10.4), с fallback на обычные сообщения
  (`/reg`, ошибка `sendWidget` или `WIDGETS_ENABLED=false`).
- Анкета из 9 шагов с «Назад», согласия на ПДн и рассылку (тексты и версии в `bot/texts.py`, `bot/services/registration.py`).
- Лимит 75 при 60 местах, лист ожидания, отмена с автоматической передачей места.
- PDF-билет с QR `VTL1.<event_id>.<ticket_no>.<sig>` (без ПДн) и `verify_ticket()` как задел под чекин.
- Напоминания T−24ч и T−2ч, смена статусов билета, просьба оценить встречу после T+3ч.
- Админка: `/stats`, `/export`, `/list`, `/link`, `/broadcast`, `/event`, `/event_set`.
- Почта как задел: при пустом `SMTP_HOST` выключена.

Сверка брифа с документацией виджетов: [docs/widgets_api_check.md](docs/widgets_api_check.md).

## Структура

```
bot/
  __main__.py        запуск: python -m bot
  app.py             сборка диспетчера, allowed_updates, профиль бота
  config.py          переменные окружения
  texts.py           все тексты (согласия дословно)
  handlers/          start (/start, /reg, /ticket, «Иду»), form (анкета), user_commands,
                     admin, widget_queries (апдейты виджетов, которых нет в aiogram)
  services/          registration (места и лист ожидания), tickets (HMAC/QR), pdf_ticket,
                     delivery (что и как отправить), reminders, mailer, admin, validation
  widgets/           api.py (методы Bot API 10.4), render.py, service.py, templates/*.html
  db/                models.py, engine.py (SSL), fsm_storage.py (FSM в Postgres), migrations/
certs/root.crt       корневой сертификат Yandex Cloud
fonts/               DejaVu Sans (кириллица для PDF)
tests/
```

## Переменные окружения

Смотри `.env.example`. Обязательны `BOT_TOKEN`, `DATABASE_URL` и `TICKET_HMAC_SECRET` (не меньше 32 байт).
`ADMIN_IDS` задаётся Telegram ID через запятую. Пустые `PRIVACY_POLICY_URL`, `CLUB_CHANNEL_URL` и `CLUB_CHAT_URL`
допустимы, бот покажет заглушки.

> `TICKET_HMAC_SECRET` после старта регистрации не меняйте: QR уже выданных билетов перестанут проходить проверку.

## Запуск локально

```bash
uv sync                      # или: python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env         # заполнить; для локальной БД: DATABASE_URL=postgresql://localhost/vitalis?sslmode=disable
set -a; source .env; set +a
uv run alembic upgrade head  # схема + первая встреча
uv run python -m bot
```

Тесты поднимают свой PostgreSQL через `pgserver`, внешняя БД не нужна:

```bash
uv run pytest
```

Чтобы гонять тесты на своей БД, задайте `TEST_DATABASE_URL`. Тесты виджетов, которые проверяют JS, требуют Node.js;
без него они пропускаются.

## Yandex Managed PostgreSQL

Требование 152-ФЗ (локализация): все ПДн, включая незавершённые анкеты (FSM), лежат только в этой БД.
Поэтому FSM хранится в Postgres, а не в Redis на Railway.

1. Кластер PostgreSQL 16 в Yandex Cloud. В настройках хоста включите **публичный доступ**: у Railway нет статичных IP.
2. В группе безопасности откройте входящий TCP 6432.
3. Создайте БД и пользователя, затем соберите строку:
   `postgresql://<user>:<pass>@<host>.mdb.yandexcloud.net:6432/<db>?sslmode=verify-full`
   (FQDN хоста указан на странице кластера: для verify-full имя должно совпадать с сертификатом).
4. Корневой сертификат положите в `certs/root.crt`:
   ```bash
   curl -fsSL https://storage.yandexcloud.net/cloud-certs/CA.pem -o certs/root.crt
   ```
   и задайте `PG_SSL_ROOT_CERT=certs/root.crt`. Бот проверяет и цепочку, и имя хоста.
5. Перед кластером стоит пулер (Odyssey, порт 6432). В коде отключён кэш подготовленных выражений asyncpg,
   поэтому бот работает с любым режимом пулинга.

## Деплой на Railway

1. New Project → Deploy from GitHub repo → `vitalis-bot`.
2. Variables: всё из `.env.example`.
3. Сборка и старт описаны в `railway.json`: `alembic upgrade head && python -m bot`, одна реплика.
   Вторая реплика не нужна: long polling допускает только один процесс на токен. Напоминания защищены от
   дублей и при кратком перекрытии во время деплоя.
4. Python 3.12 берётся из `.python-version`, зависимости из `uv.lock` / `requirements.txt`.

## Первая встреча и следующие

Первую встречу (19.11.2026, 17:00 МСК, 60 мест / лимит 75) создаёт сид-миграция `0002`. Её данные лежат в
`bot/db/seed_data.py`. Следующие встречи заводятся строкой в таблице `events`, без изменения кода:

```sql
INSERT INTO events (title, speaker, starts_at, venue_text, venue_url, seats, registration_limit, is_open)
VALUES ('Встреча #2', 'Имя спикера', '2026-12-10 14:00+00', 'Адрес', 'https://yandex.ru/maps/?text=…', 60, 75, true);
```

Бот показывает ближайшую открытую встречу, которая ещё не началась. Регистрация закрывается сама в `starts_at`.

Через бота у текущей встречи можно поменять `venue_text`, `venue_url`, `is_open`, `registration_limit` и
`starts_at`: `/event_set registration_limit 80`. При смене адреса или времени карточка-виджет создаётся заново
(HTML виджета неизменяем). `starts_at` нужен для ручной проверки напоминаний: перенесите встречу на ближайшие
минуты и смотрите, как приходят сообщения.

## Виджеты

- Карточка встречи: один shared-виджет на встречу, `events.shared_widget_id`. Создаётся первым `sendWidget(is_shared)`.
  Счётчик мест обновляется через `editSharedWidgetData` не чаще раза в секунду, открытая карточка опрашивает
  его раз в 10 с (`cache_time`).
- Билет: обычный виджет со статусами registered → soon → live → done (оценка) / cancelled в `chat_data`.
- Бюджет 4 КБ после zlib-9 проверяется тестами. Сейчас карточка весит ~1,5 КБ, билет ~1,7 КБ.
- Аварийный выключатель: `WIDGETS_ENABLED=false` и перезапуск.

## Ручная приёмка

1. `https://t.me/vitalis_business_club_bot?start=test` → регистрация до PDF-билета.
2. `/stats`: источник `test` в разбивке.
3. `/cancel` → место ушло человеку из листа ожидания (для проверки: `/event_set registration_limit 1`).
4. `/event_set starts_at <через 2ч 5мин>` → напоминание за 2 часа и смена статусов приходят по одному разу.
5. `/export` открывается в Excel без кракозябр.

## Открытые вопросы [TODO]

Политика ПДн (URL), ссылки на канал и чат, название агентства (`AGENCY_NAME` в `bot/texts.py`), логотип
(сейчас в PDF текстовый), SMTP, формат списка для площадки (`/list` сейчас текстом).
