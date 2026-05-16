# AGENTS.md — контекст для ИИ (Cursor)

## Язык ответов

Отвечайте пользователю **на русском языке** (ясный технический стиль), если он явно не попросил другой язык. Комментарии в коде и имена в репозитории не меняйте только ради этого — следуйте существующим соглашениям проекта.

## Что это за проект

Python-пакет **cookie-grabber**: нагул cookies в **ADS Power** через **Playwright** (подключение по **CDP** `connect_over_cdp`), очередь/метрики в **Google Sheets** (service account), ротация **прокси**. Управление: **графическое меню** (CustomTk в отдельном окне под Windows или терминальное **Rich**). Многопоточность: **ThreadPoolExecutor**, по одному воркеру на строку таблицы (профиль ADS).

## Запуск и установка

- Корень репозитория = рабочая директория; настройки резолвят пути относительно неё (`AppSettings.resolve`).
- Установка: `pip install -e .` из корня; браузер: `playwright install chromium`.
- Точка входа: **`cookie-grabber`** или `python -m cookie_grabber.main`.
- **Windows**, отдельное окно: по умолчанию поднимается **GUI** (`customtkinter`); процесс-хост остаётся в терминале Cursor (**логи воркеров там же**). Дочерний процесс стартует как `python -m cookie_grabber.gui_spawn` (без тяжёлого импорта `main.py`). При сбое GUI см. файл **`gui_client_last_error.log`** в корне проекта и сообщения в журнале хоста.
- **`--inline-menu`** — только терминальное меню (**Rich**) в текущей консоли (удобно на Linux/macOS или без окна GUI).
- **`--detach-console-menu`** (только Windows) — отдельная **консоль** с Rich-клиентом вместо GUI.
- Сохранение из GUI пишет **`config/settings.yaml`** (см. `save_user_settings()` в [`src/cookie_grabber/config/settings.py`](src/cookie_grabber/config/settings.py)); перед «Старт» на хосте выполняется **RELOAD**.
- **Windows exe:** сборка [`scripts/build_win.ps1`](scripts/build_win.ps1) → `dist/CookieGrabber/CookieGrabber.exe`. Рядом с exe — `config/`, `secrets/`, `data/` (корень данных = каталог exe, см. [`runtime_paths.py`](src/cookie_grabber/runtime_paths.py)). Релизы и автообновление — GitHub Releases [`edward-ruki-nogi/Cookie_Grabber`](https://github.com/edward-ruki-nogi/Cookie_Grabber); кнопка «Проверить обновление» в GUI.

## Флаги остановки потоков (`RunControl`)

Общий объект **`RunControl`** ([`control/runtime_control.py`](src/cookie_grabber/control/runtime_control.py)) передаётся воркерам: поле **`shutdown`** — это **`threading.Event`** (жёсткая остановка). **Не путать** с методом пула **`ThreadPoolExecutor.shutdown()`**.

- **`shutdown` (флаг):** проверять **в начале** каждого нового логического блока в контуре воркера (профиль, выбор прокси, цикл URL, `farm_single_url`, утилиты с ожиданием). При установленном флаге — **не начинать** новых действий, выходить как можно раньше. В **любом цикле** и **перед каждым достаточно длительным шагом** (ожидание, sleep, переход между URL, опрос ресурсов) снова проверять **`shutdown`**.
- **`safe_stop`:** мягкая остановка — **не начинать следующий URL** после завершённого текущего; между визитами выйти из цикла и дойти до `finally` (закрытие браузера, запись в лист).
- **`pause`:** пауза между URL (можно использовать из терминального RPC).
- **`interruptible_sleep` / паузы в `timing`:** длинный sleep разбивать на короткие интервалы с проверкой **`shutdown`**.

Ограничение: уже идущий вызов Playwright (`goto`, `wait_for_load_state`) нельзя прервать мгновенно — после установки **`shutdown`** выход возможен после **таймаута** операции или закрытия контекста/браузера.

Маппинг с UI/RPC: **«Завершение» → `SAFE_STOP`**, **«Стоп» → `SHUTDOWN`**.

## Конфигурация

- **Дефолты** встроены в модуль [`src/cookie_grabber/config/settings.py`](src/cookie_grabber/config/settings.py) как константа **`_DEFAULT_SETTINGS_YAML`** (отдельного файла дефолтов в репозитории нет).
- **Переопределения:** **`config/settings.yaml`** (часто не в git по `.gitignore`) — deep-merge поверх встроенных дефолтов, см. `load_settings()` в том же модуле.
- Сервис-аккаунт Google: **`secrets/service_account.json`** (не коммитить).
- Важные сайты: список в YAML + чекбокс `enabled`; у каждого `id` должен быть ключ в `google_sheets.important_site_columns` (буква столбца).
- Массовка: файл путей URL, по умолчанию [`data/mass_sites.txt`](data/mass_sites.txt); порядок **shuffle** перед обходом; лимит URL задаётся **`farming.mass_sites_count`** (0 = все строки файла).
- Нагул по времени: блок **`farming`** — **`important_sites_minutes_per_site`** (минуты wall-clock на каждый включённый важный сайт за сессию строки, 0 = пропуск блока), **`mass_sites_minutes_per_site`** (минуты на каждый выбранный URL второстепенного списка, 0 = пропуск массовки), **`mass_sites_count`** (сколько URL из файла после shuffle; 0 = без лимита). Реализация: цикл вызовов **`farm_single_url`** внутри бюджета — [`farming/flow.py`](src/cookie_grabber/farming/flow.py) (`farm_single_url_for_minutes`).
- Опционально **`farming.show_synthetic_mouse`**: оверлей «курсора» в окне браузера (CDP-события сами системный курсор не двигают); по умолчанию выключено, чекбокс «Отображение курсора» на вкладке «Основные» в GUI.
- Лимит стартов за запуск: **`accounts_per_run`** (в GUI вкладка «Многопоток», поле «Выполнений», в YAML число **0** = без лимита; в поле GUI для 0 показывается **∞**). Считаются только профили, с которыми **начата** новая выдача строки (резерв до `acquire_next_row_index`); уже идущие сессии квота **не прерывает** и **`safe_stop` для этого не выставляется**. Пропуск строки (`skip_*`, `reset_counter`) откатывает резерв слота.

**При изменении схемы или дефолтных значений настроек** правьте согласованно **два слоя** (и при необходимости таблицу/AGENTS):

1. **`src/cookie_grabber/config/settings.py`** — строка **`_DEFAULT_SETTINGS_YAML`**, dataclass-поля (`FarmingConfig` и др.), `_dict_to_ads` / `_dict_to_sheets`, `_parse_important_sites`, `app_settings_to_dict`, `_validate`.
2. **`config/settings.yaml`** — локальный файл (если есть): добавьте ключ или обновите значение здесь же, когда меняете то, что уже переопределено на диске; иначе merge сохранит старую строку из YAML.

Строковые статусы **`status_values.*`** (ожидание/прогрев/создан/удалён/сбой) должны совпадать с текстом в Google Таблице; при ошибке воркер **восстанавливает** статус строки к значению на момент начала обработки (не выставляет «Ошибка»). Если исходный статус был **`status_values.empty`** («Ожидает»), при откате дополнительно **останавливается и удаляется** профиль ADS, очищается столбец `ads_profile_id_column` (G).

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
| Настройки | `src/cookie_grabber/config/settings.py` (`_DEFAULT_SETTINGS_YAML` + `load_settings`) |
| ADS API | `ads/ads_power_api.py` |
| Sheets | `sheets/google_sheets_api.py`, `sheets/atomic.py`, `sheets/models.py` |
| Прокси | `grabber_proxy.py`: «9 static» через вендорный `gala_9static_proxy` ([`vendor/gala-9static-proxy/`](vendor/gala-9static-proxy/); порт = три цифры префикса + суффикс `01`–`99`, API `:10101`) |
| Воркер профиля | `workers/profile_worker.py` |
| Нагул страницы | `farming/flow.py`, `farming/selectors.py` |
| Поведение мыши | `behavior/bezier.py`, `cdp_mouse.py`, `timing.py` |
| Логи для live-экрана | `log_bus/setup.py` |

## GUI: дизайн меню и новые элементы

Все изменения интерфейса делаются в [`src/cookie_grabber/ui/app_window.py`](src/cookie_grabber/ui/app_window.py). **Новые элементы должны визуально и по поведению совпадать с уже принятым дизайном**, а не «вставляться» со сторонними стилями.

**Глобальная тема (не менять без причины):** после импорта CustomTkinter — `set_appearance_mode("dark")`, `set_default_color_theme("blue")`; корневое окно — `CTk`, заголовок «Cookie Grabber».

**Верхняя панель (команды):** `CTkFrame(..., fg_color="transparent")`, `pack(fill="x", padx=10, pady=10)`; кнопки — `pack(side="left", padx=4)`. **«Старт»** — зелёная пара цветов `fg_color=("#2FA572", "#1F7A4A")`, `hover_color=("#38B882", "#258A5E")`, ширина порядка **100**. **«Стоп»** — акцент на опасность: `fg_color=("gray35", "#5c2121")`, `hover_color=("gray45", "#7a2828")`. Остальные кнопки панели — обычный стиль CTk, сопоставимая ширина (около **100–110**), без случайных `fg_color`, если это не осмысленный второй акцент.

**Блок настроек:** раскрывается кнопкой «Настройки»; внутри — `CTkTabview` с вкладками. На **каждой вкладке** контент кладите в **`CTkScrollableFrame`**, `pack(fill="both", expand=True)` и **`grid_columnconfigure(1, weight=1)`**, чтобы поля тянулись по ширине.

**Заголовки секций внутри вкладки:** `CTkLabel(..., font=("", 13, "bold"))`, выравнивание `sticky="w"`, отступы как у существующих блоков (`padx=8`, вертикальные `pady` согласованы с соседними секциями).

**Строки формы «подпись + поле»:** повторяйте шаблон `add_labeled_entry`: колонка **0** — подпись (`sticky="w", padx=8, pady=4`), колонка **1** — `CTkEntry` (`sticky="ew", padx=8, pady=4`). Ключи в словаре `entries` — стабильные строковые id (как у текущих полей).

**Чекбоксы и второстепенные кнопки в теле вкладки:** одна колонка на всю ширину — `columnspan=2`, `sticky="w", padx=8, pady=4` (чекбоксы) или `pady=8` для отдельно стоящих кнопок (ширина порядка **220**, если это «действие», а не панельная команда).

**После изменения состава виджетов** вызывайте **`fit_window()`** (и при раскрытии/сворачивании панели настроек — как в существующем `toggle_settings`).

**Данные:** любое новое поле должно участвовать в **`load_fields_from_disk()`** и **`gather_settings()`** (или явно читаться из `load_settings` как «только из YAML», если не редактируется в GUI). Сохранение идёт через **`save_user_settings`** + **`validate_app_settings`** — не обходите валидацию.

**Тексты в UI:** пользовательские подписи — на **русском**, в одном тоне с текущими формулировками; технические имена полей YAML в подписях избегайте, если есть понятное имя (как «Имя листа» для `worksheet_name`).

## Соглашения для правок

- Менять только то, что нужно задаче; не переименовывать публичные модули без причины (`ads_power_api` / `google_sheets_api`).
- Не коммитить секреты и локальный `config/settings.yaml` с реальными ID.
- При изменении схемы YAML см. блок **«При изменении схемы…»** выше (**`_DEFAULT_SETTINGS_YAML`** + dataclass/парсинг в **`settings.py`** + при необходимости **`settings.yaml`** и этот файл, если меняется контракт для ИИ).

## Тесты

Отдельного набора тестов в репозитории пока нет; быстрая проверка: `python -m compileall -q src`, импорт `cookie_grabber`.
