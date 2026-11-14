# Сверка брифа (раздел 5) с документацией Telegram

Источники: https://core.telegram.org/bots/widgets, https://core.telegram.org/bots/api (Bot API 10.4), проверено 14.11.2026.

## Сигнатуры, которые используем

| Метод | Параметры (обязательные **жирным**) | Возвращает |
|---|---|---|
| `sendWidget` | **chat_id**, **html** (1–32768 байт, ≤4096 после zlib-9), width, height, public_data (0–1024 байт), chat_data (0–1024 байт), caption (≤1024 симв.), parse_mode, caption_entities, show_caption_above_media, allow_queries, is_shared, disable_notification, protect_content, reply_markup, … | Message с полем `widget` (`widget.id` у shared) |
| `sendSharedWidget` | **chat_id**, **widget_id**, chat_data, caption, parse_mode, reply_markup, … (html и public_data НЕ передаются) | Message |
| `editMessageWidgetData` | chat_id + message_id (или inline_message_id), public_data, chat_data. Пропущенное поле не меняется, пустая строка очищает | True |
| `editSharedWidgetData` | **widget_id**, **public_data**, cache_time (5–3600) | True |
| `answerWidgetQuery` | **widget_query_id**, data (0–1024 байт) | True |

Update: поля `widget_query` (WidgetQuery: id, from, chat?, message_id?, inline_message_id?, chat_instance, chat_type?, type, data, widget_id?) и `shared_widget_query` (только из пересланных копий: id, from, type, data, widget_id).

## Что расходится с брифом или уточняет его

1. **Нет отдельного метода создания shared-виджета.** Shared-виджет создаётся первым `sendWidget` с `is_shared=true` (+ `allow_queries=true`, иначе «Иду» не сработает). `widget.id` читаем из ответа и кладём в `events.shared_widget_id`; дальше всем шлём `sendSharedWidget`. HTML и флаги у shared-виджета неизменяемы: поменяли шаблон карточки → нужен новый shared-виджет (сбрасываем `shared_widget_id`).
2. **«Живой счётчик» не мгновенный.** Открытый на экране shared-виджет получает новый `public_data` только если сам вызвал `TelegramWidget.watchPublicData()`, и тогда клиент опрашивает сервер раз в `cache_time` (по умолчанию 30 с; меняется только через `editSharedWidgetData`, диапазон 5–3600). Ставим `cache_time=10`. Лимит 1 правка в секунду подтверждён. Документация советует класть ревизию `"r"` в состояние, чтобы виджет не откатывался на старые данные: делаем.
3. **Кнопка «В календарь».** `openLink` принимает только `https:`, не длиннее **256 символов**, не t.me. Ссылки на `.ics` у нас нет (бот на long polling, своего веб-сервера нет), а ссылка Google Calendar с кириллицей в URL-кодировке легко выходит за 256 символов. Решение: короткая ссылка Google Calendar с латинским заголовком и без описания, длину проверяем тестом. `.ics` дополнительно отправляем файлом в чат по кнопке fallback-билета.
4. **Ответ на запрос ничего не меняет.** `answerWidgetQuery` несёт только `data` (без алертов, уведомлений и смены состояния). Смену состояния делаем отдельным `editMessageWidgetData`. Отвечать нужно на каждый запрос, иначе у виджета TIMEOUT.
5. **`editMessageWidgetData` считается отправленным сообщением** для лимитов бота. Смена статусов билета по расписанию идёт через ту же очередь с паузой 35 мс, что и напоминания.
6. **«Message is not modified»** при правке без изменений: считаем успехом.
7. **В проде виджеты автоматически protected content** (нельзя переслать). С брифом согласуется, просто фиксируем.
8. **У запроса есть поле `type`** (data / sign_data / sent_transaction, кошелёк TON). Обрабатываем только `data`, остальное отклоняем.
9. **Состояние билета.** `chat_data` ≤1024 байт (≈512 кириллических символов). Кладём туда имя, номер, статус и ревизию, а дату и место встречи вшиваем в HTML при отправке: так HTML остаётся шаблоном, и тест на 4 КБ проверяет его с реалистичными данными.
10. **Обратный отсчёт на клиенте** работает: таймеры есть, но в фоне срабатывают не чаще раза в секунду. Нормально для отсчёта.
11. **Подпись (caption).** Если подпись под виджетом, время сообщения рисуется в подписи и не перекрывает виджет. Подпись всегда ставим снизу.
12. **aiogram.** Методы и апдейты виджетов aiogram не знает: вызываем `api.telegram.org` напрямую через сессию бота, апдейты `widget_query`/`shared_widget_query` достаём из `Update.model_extra`, `allowed_updates` передаём явным списком.

## Не из раздела 5, но важно для 152-ФЗ

FSM-хранилище: бриф допускает Redis, но в состоянии анкеты лежат ФИО, телефон и почта. Redis на Railway находится за пределами РФ, это нарушение локализации. Поэтому FSM храним в той же Yandex Managed PostgreSQL (своё хранилище aiogram, таблица `fsm_state`).
