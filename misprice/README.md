# Misprice Hunter — GitHub Pages + Actions

Поиск ошибочно низких цен (misprice) на украинских маркетплейсах (Comfy, Citrus).
Запускается кнопкой из браузера, выполняется в GitHub Actions, результаты публикуются на GitHub Pages.

## ⚠️ Важно про безопасность токена

**НИКОГДА не коммитьте GitHub PAT в репозиторий и не публикуйте его в чатах!**

Токен в этом проекте хранится только в `localStorage` вашего браузера и отправляется исключительно в `api.github.com` по HTTPS. В коде токена нет — вы вводите его в поле на странице.

Если вы случайно опубликовали токен — **немедленно отзовите его**:
https://github.com/settings/tokens → Delete → создайте новый.

---

## 🚀 Деплой за 5 шагов

### Шаг 1. Создайте репозиторий

Если репо `gavnenko123/rycar-preview` уже есть — используйте его. Если нет — создайте публичный репозиторий на GitHub.

### Шаг 2. Загрузите файлы

Загрузите содержимое папки `github-pages/` в корень репозитория. Структура должна быть такой:

```
rycar-preview/
├── index.html
├── app.js
├── style.css
├── README.md
├── scripts/
│   └── monitor_actions.py
├── docs/
│   └── .gitkeep
└── .github/
    └── workflows/
        └── scan.yml
```

### Шаг 3. Включите GitHub Pages

1. Зайдите в репозиторий → **Settings** → **Pages**
2. В секции **Build and deployment**:
   - Source: **Deploy from a branch**
   - Branch: `main` / root (или `main` / `docs` — оба варианта работают, т.к. `index.html` в корне)
   - Нажмите **Save**
3. Через 1-2 минуты сайт будет доступен по адресу:
   `https://gavnenko123.github.io/rycar-preview/`

### Шаг 4. Создайте GitHub PAT

1. Зайдите на https://github.com/settings/tokens
2. **Generate new token (classic)**
3. Note: `Misprice Hunter`
4. Expiration: на ваш выбор (90 дней рекомендуется)
5. Scopes — отметьте:
   - ✅ `repo` (полностью)
   - ✅ `workflow`
6. **Generate token** → скопируйте значение (начинается с `ghp_`)

### Шаг 5. Запустите первый скан

1. Откройте `https://gavnenko123.github.io/rycar-preview/`
2. В поле «GitHub PAT» вставьте ваш токен
3. В полях «Власник репо» / «Назва репо» — оставьте `gavnenko123` / `rycar-preview`
4. В «Запити» — оставьте по умолчанию или впишите свои через запятую
5. Нажмите **▶ Запустити скан**

Скрипт:
- Найдёт workflow «Price Scan»
- Запустит его через GitHub API
- Будет опрашивать статус (≈2 минуты)
- По завершении автоматически обновит результаты на странице

---

## 📋 Как это работает

```
[Браузер]                    [GitHub API]              [GitHub Actions]
    │                              │                          │
    │── POST /dispatches ────────▶│                          │
    │                              │── запускает workflow ───▶│
    │                              │                          │── pip install
    │                              │                          │── playwright install
    │◀── polling runs ────────────│                          │── scrape Comfy/Citrus
    │                              │                          │── save docs/results.json
    │                              │                          │── git commit + push
    │                              │                          │── ✅ done
    │── GET raw/.../results.json ─▶│                          │
    │◀── JSON с алертами ─────────│                          │
    │                              │                          │
    └── рендер таблицы алертов                                │
```

---

## 🔧 Автоматический запуск по расписанию

В `.github/workflows/scan.yml` уже настроен cron — каждый час:

```yaml
on:
  workflow_dispatch: ...
  schedule:
    - cron: '0 * * * *'   # каждый час
```

Чтобы получать оповещения о новых алертах:
1. Зайдите в репо → **Actions** → **Price Scan**
2. Нажмите ⋮ (три точки) → **Workflow notifications** → **All workflows**

Чтобы выключить авто-запуск — закомментируйте строки `schedule:` и `cron:`.

---

## 🎯 Что считается misprice

Товар флагуется если выполняется **хотя бы одно** условие:
- Цена < **20% от медианы** по категории (80%+ скидка от рынка)
- Z-score (по MAD) < **−3** (статистический выброс)
- Цена < **30% медианы** (ниже ожидаемого минимума)

Дополнительно:
- Аксессуары (сумки, чохлы, скло, миші...) **отсекаются** по ключевым словам
- Минимальная цена по категории (ноутбуки от 5000₴, GPU от 3000₴ и т.д.)
- Каждое попадание получает `misprice_score` (0–100) и уровень: 🔴 critical / 🟠 high / 🟡 medium / ⚪ low

---

## 📁 Файлы результатов (в `docs/`)

| Файл | Описание |
|---|---|
| `results.json` | Полный отчёт: метаданные, статистика, все алерты, все товары |
| `results_alerts.csv` | Только misprice-алерты (severity, score, цена, медиана, reason) |
| `results_all.csv` | Все отсканированные товары |
| `scan_log.txt` | Лог выполнения скрипта |

Все файлы доступны для скачивания кнопками на странице + по прямым ссылкам:
`https://raw.githubusercontent.com/gavnenko123/rycar-preview/main/docs/results.json`

---

## 🐛 Troubleshooting

### «Workflow не найден»
- Проверьте, что файл `.github/workflows/scan.yml` закоммичен в `main`
- Зайдите в репо → **Actions** — должен быть виден workflow «Price Scan»

### «403 Forbidden» при dispatch
- У токена нет прав `workflow` — пересоздайте с правильными scopes
- Репозиторий приватный — нужен полный scope `repo`

### «Workflow завершился со статусом failure»
- Откройте run на GitHub Actions → посмотрите логи
- Чаще всего: Playwright не установился (мало памяти runner) или сайт заблокировал
- Скрапер устойчив к блокировкам — даже если 1 сайт не ответит, остальные дадут данные

### Результаты не обновляются на странице
- GitHub Pages кеширует. Фронтенд добавляет `?t=timestamp` для обхода кеша
- Подождите 30-60 сек после завершения workflow (Pages деплоится не мгновенно)
- Нажмите **↻ Оновити результати**

### Сайты блокируют (Comfy/Citrus отдают 0 товаров)
- Cloudflare/Imperva иногда блокируют IP GitHub Actions
- Попробуйте запустить ещё раз через 10-15 минут
- Если стабильно 0 — сайты усилили защиту, нужны residential-прокси

---

## 📞 Поддерживаемые сайты

| Сайт | Статус | URL |
|---|---|---|
| Comfy | ✅ Работает | comfy.ua |
| Citrus | ✅ Работает | citrus.ua |
| Rozetka | ❌ Cloudflare | rozetka.com.ua |
| Allo | ❌ Защита | allo.ua |
| Foxtrot | ❌ Защита | foxtrot.com.ua |
| Prom | ❌ Rate-limit | prom.ua |

Для добавления нового сайта — отредактируйте `scripts/monitor_actions.py`, добавьте класс-скрапер и JS-экстрактор.
