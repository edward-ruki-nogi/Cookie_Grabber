# AGENTS.md — контекст для ИИ (Cursor)

## Что это за проект

Python-пакет **cookie-grabber**: нагул cookies в **ADS Power** через **Playwright** (подключение по **CDP** `connect_over_cdp`), очередь/метрики в **Google Sheets** (service account), ротация **прокси**. Управление: **графическое меню** (CustomTk в отдельном окне под Windows или терминальное **Rich**). Многопоточность: **ThreadPoolExecutor**, по одному воркеру на строку таблицы (профиль ADS).

## Запуск и установка

- Корень репозитория = рабочая директория; настройки резолвят пути относительно неё (`AppSettings.resolve`).
- Установка: `pip install -e .` из корня; браузер: `playwright install chromium`.
- Точка входа: **`cookie-grabber`** или `python -m cookie_grabber.main`.
- **Windows**, отдельное окно: по умолчанию поднимается **GUI** (`customtkinter`); процесс-хост остаётся в терминале Cursor (**логи воркеров там же**). Дочерний процесс стартует как `python -m cookie_grabber.gui_spawn` (без тяжёлого импорта `main.py`). При сбое GUI см. файл **`gui_client_last_error.log`** в корне проекта и сообщения в журнале хоста.
- **`--inline-menu`** — только терминальное меню (**Rich**) в текущей консоли (удобно на Linux/macOS или без окна GUI).
- **`--detach-console-menu`** (только Windows) — отдельная **консоль** с Rich-клиентом вместо GUI.
- Сохранение из GUI пишет **`config/settings.yaml`** (см. `save_user_settings()` в `config/settings.py`); перед «Старт» на хосте выполняется **RELOAD**.

## Флаги остановки потоков (`RunControl`)

Общий объект **`RunControl`** ([`control/runtime_control.py`](src/cookie_grabber/control/runtime_control.py)) передаётся воркерам: поле **`shutdown`** — это **`threading.Event`** (жёсткая остановка). **Не путать** с методом пула **`ThreadPoolExecutor.shutdown()`**.

- **`shutdown` (флаг):** проверять **в начале** каждого нового логического блока в контуре воркера (профиль, выбор прокси, цикл URL, `farm_single_url`, утилиты с ожиданием). При установленном флаге — **не начинать** новых действий, выходить как можно раньше. В **любом цикле** и **перед каждым достаточно длительным шагом** (ожидание, sleep, переход между URL, опрос ресурсов) снова проверять **`shutdown`**.
- **`safe_stop`:** мягкая остановка — **не начинать следующий URL** после завершённого текущего; между визитами выйти из цикла и дойти до `finally` (закрытие браузера, запись в лист).
- **`pause`:** пауза между URL (можно использовать из терминального RPC).
- **`interruptible_sleep` / паузы в `timing`:** длинный sleep разбивать на короткие интервалы с проверкой **`shutdown`**.

Ограничение: уже идущий вызов Playwright (`goto`, `wait_for_load_state`) нельзя прервать мгновенно — после установки **`shutdown`** выход возможен после **таймаута** операции или закрытия контекста/браузера.

Маппинг с UI/RPC: **«Завершение» → `SAFE_STOP`**, **«Стоп» → `SHUTDOWN`**.

## Конфигурация

- База: [`config/default_settings.yaml`](config/default_settings.yaml).
- Переопределения: **`config/settings.yaml`** (не в git по `.gitignore`) — deep-merge поверх дефолтов, см. `load_settings()` в [`src/cookie_grabber/config/settings.py`](src/cookie_grabber/config/settings.py).
- Сервис-аккаунт Google: **`secrets/service_account.json`** (не коммитить).
- Важные сайты: список в YAML + чекбокс `enabled`; у каждого `id` должен быть ключ в `google_sheets.important_site_columns` (буква столбца).
- Массовка: файл путей URL, по умолчанию [`data/mass_sites.txt`](data/mass_sites.txt); порядок **shuffle** при построении плана на сессию.
- Доли wall-clock между группами «важные / массовка»: `session_time_budget.important_share` + `mass_share` (= 1.0 в режиме `shares`).

## Google Таблица (модель данных)

- **Одна строка = один профиль ADS** (`profile_id_column`).
- Накопительное время: по столбцу на каждый важный сайт + один столбец суммы по массовке; статус, пометки, последнее обновление (Europe/London, ISO-строка).
- Запись: **`spreadsheets.values.batchUpdate`** с **`valueInputOption: RAW`** — см. [`src/cookie_grabber/sheets/atomic.py`](src/cookie_grabber/sheets/atomic.py). Запись в лист сериализуется **lock**-ом в [`google_sheets_api.py`](src/cookie_grabber/sheets/google_sheets_api.py) из-за многопоточности.

## ADS Power

- HTTP-клиент: [`src/cookie_grabber/ads/ads_power_api.py`](src/cookie_grabber/ads/ads_power_api.py) (намеренно **не** `client.py`, чтобы не путать с Sheets).
- Поддерживаются **`api_version: v1`** (GET start/stop) и **`v2`** (POST); база URL и пути в YAML. Опционально `api_key` → заголовок `Authorization: Bearer …`.
- После старта используется **`ws.puppeteer`** для Playwright.

## Архитектура пакета `cookie_grabber`

| Область | Путь |
|--------|------|
| Меню / оркестратор | `main.py` |
| Точка быстрого старта GUI | `gui_spawn.py` (`python -m cookie_grabber.gui_spawn`) |
| GUI-клиент | `ui/app_window.py` |
| Флаги остановки | `control/runtime_control.py` |
| Настройки | `config/settings.py` |
| ADS API | `ads/ads_power_api.py` |
| Sheets | `sheets/google_sheets_api.py`, `sheets/atomic.py`, `sheets/models.py` |
| Прокси | `grabber_proxy.py`: сценарий «9 static» через `gala_9static_proxy` (порт `60{port_order}{suffix}`, API `:10101`) |
| План сессии | `workers/session_plan.py` |
| Воркер профиля | `workers/profile_worker.py` |
| Нагул страницы | `farming/flow.py`, `farming/selectors.py` |
| Поведение мыши | `behavior/bezier.py`, `cdp_mouse.py`, `timing.py` |
| Логи для live-экрана | `log_bus/setup.py` |

## Соглашения для правок

- Менять только то, что нужно задаче; не переименовывать публичные модули без причины (`ads_power_api` / `google_sheets_api`).
- Не коммитить секреты и локальный `config/settings.yaml` с реальными ID.
- При изменении схемы YAML обновляйте **`default_settings.yaml`**, парсинг и валидацию в **`settings.py`**, и этот файл, если меняется контракт.

## Тесты

Отдельного набора тестов в репозитории пока нет; быстрая проверка: `python -m compileall -q src`, импорт `cookie_grabber`.
