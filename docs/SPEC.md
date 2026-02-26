# Техническое задание: AI Doc Translator

## 1. Назначение системы

Веб-приложение для автоматического перевода документов формата PDF, TXT и HTML с одного языка на другой с помощью LLM (GigaChat, Ollama). Система сохраняет структуру и форматирование оригинала, поддерживает пользовательские глоссарии, историю переводов и асинхронную обработку.

---

## 2. Функциональные требования

### 2.1 Загрузка и управление переводом

- **FR-01** Пользователь загружает файл (PDF / TXT / HTML) через веб-форму методом `multipart/form-data`
- **FR-02** Пользователь выбирает язык назначения из списка (язык источника определяется автоматически)
- **FR-03** После загрузки система возвращает `job_id`, пользователь видит прогресс в реальном времени
- **FR-04** Пользователь может скачать переведённый файл в исходном формате
- **FR-05** Пользователь может отменить задачу в статусе `pending` или `running`
- **FR-06** Максимальный размер загружаемого файла — 50 МБ (конфигурируется)

### 2.2 Глоссарий

- **FR-07** Пользователь может добавлять пары «термин оригинала → термин перевода»
- **FR-08** Глоссарий применяется автоматически к каждому переводимому чанку
- **FR-09** Пользователь может редактировать и удалять термины
- **FR-10** Глоссарий хранится в PostgreSQL, привязан к `user_id`

### 2.3 История переводов

- **FR-11** Список всех завершённых переводов пользователя с метаданными (файл, языки, дата, статус)
- **FR-12** Повторная загрузка результата из истории
- **FR-13** Удаление записи из истории (с удалением файла с диска)

### 2.4 Автоматическое определение языка

- **FR-14** Язык источника определяется по первым ~1000 символам документа через LLM или `langdetect`
- **FR-15** Если автоопределение неуверенно (score < 0.9), пользователю предлагается выбрать вручную

### 2.5 Сохранение структуры документа

- **FR-16 PDF:** сохраняются заголовки (по размеру шрифта), параграфы, таблицы, списки
- **FR-17 HTML:** сохраняется весь DOM-дерево, переводятся только текстовые узлы
- **FR-18 TXT:** сохраняется структура абзацев (двойной перенос строки)

---

## 3. Нефункциональные требования

- **NFR-01** Язык: Python 3.11+, пакет-менеджер `uv`
- **NFR-02** Все I/O операции — `async/await` (SQLAlchemy async, httpx, aiofiles)
- **NFR-03** Время ответа API на upload — не более 500 мс (сама задача выполняется асинхронно)
- **NFR-04** Логирование через `logging` с уровнями DEBUG/INFO/WARNING/ERROR; каждая запись содержит `job_id`
- **NFR-05** Все секреты (ключи API, URL БД) — только через переменные окружения (`.env`)
- **NFR-06** Покрытие тестами — не менее 80% для бизнес-логики
- **NFR-07** Форматирование — `black` (88 символов), линтер — `ruff`, типизация — `mypy --strict`
- **NFR-08** Документация — Google-style docstrings на всех публичных функциях и классах

---

## 4. Архитектура системы

```
Browser
  │ HTTP
  ▼
FastAPI
  ├── enqueue ──► AsyncQueue ──► LangGraph Agent ──► GigaChat / Ollama
  ├── read/write ◄──────────────────────────────────► PostgreSQL
  └── serve files ◄────────────────────────────────── FileSystem
```

---

## 5. Структура проекта

```
ai_doc_translator/
├── app/
│   ├── main.py                   # FastAPI app, lifespan, монтирование роутеров
│   ├── config.py                 # Pydantic Settings (из .env)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── translations.py       # upload / status / download / cancel
│   │   ├── glossaries.py         # CRUD глоссария
│   │   └── history.py            # список и удаление истории
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── state.py              # TranslationState TypedDict
│   │   ├── graph.py              # сборка LangGraph графа
│   │   ├── tools.py              # LangChain Tools
│   │   └── nodes/
│   │       ├── parse_document.py
│   │       ├── detect_language.py
│   │       ├── chunk_document.py
│   │       ├── translate_chunk.py
│   │       ├── update_context.py
│   │       └── assemble_document.py
│   ├── services/
│   │   ├── document_parser.py    # PDF/HTML/TXT → ParsedDocument
│   │   ├── document_chunker.py   # ParsedDocument → List[Chunk]
│   │   ├── document_assembler.py # List[TranslatedChunk] → output file
│   │   ├── glossary_service.py   # DB-операции над глоссарием
│   │   ├── history_service.py    # DB-операции над историей
│   │   └── task_queue.py         # AsyncQueue + worker
│   ├── models/
│   │   ├── database.py           # async engine, session factory
│   │   ├── db_models.py          # SQLAlchemy ORM
│   │   └── schemas.py            # Pydantic request/response схемы
│   ├── templates/
│   │   ├── base.html
│   │   ├── index.html
│   │   ├── history.html
│   │   └── glossary.html
│   └── static/
│       ├── css/main.css
│       └── js/app.js
├── alembic/
│   ├── env.py
│   └── versions/
├── tests/
│   ├── conftest.py
│   ├── test_parsers.py
│   ├── test_chunker.py
│   ├── test_agent_nodes.py
│   ├── test_assembler.py
│   └── test_api.py
├── uploads/
├── results/
├── docs/
│   └── SPEC.md                   # этот файл
├── .env.example
├── alembic.ini
└── pyproject.toml
```

---

## 6. Модели данных

### 6.1 PostgreSQL (SQLAlchemy ORM)

**`users`**

| Поле | Тип | Описание |
|---|---|---|
| `id` | UUID PK | |
| `email` | VARCHAR UNIQUE NOT NULL | |
| `created_at` | TIMESTAMP | DEFAULT now() |

**`translation_jobs`**

| Поле | Тип | Описание |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK → users.id | |
| `status` | ENUM | pending / running / done / error / cancelled |
| `source_lang` | VARCHAR(8) | nullable, до детекции |
| `target_lang` | VARCHAR(8) NOT NULL | |
| `input_path` | TEXT NOT NULL | |
| `output_path` | TEXT | nullable |
| `error_msg` | TEXT | nullable |
| `chunk_total` | INT | nullable |
| `chunk_done` | INT | DEFAULT 0 |
| `created_at` | TIMESTAMP | DEFAULT now() |
| `started_at` | TIMESTAMP | nullable |
| `finished_at` | TIMESTAMP | nullable |

**`glossaries`**

| Поле | Тип | Описание |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK → users.id | |
| `source_term` | TEXT NOT NULL | |
| `target_term` | TEXT NOT NULL | |
| `created_at` | TIMESTAMP | DEFAULT now() |
| UNIQUE | (user_id, source_term) | |

**`translation_history`**

| Поле | Тип | Описание |
|---|---|---|
| `id` | UUID PK | |
| `job_id` | UUID FK → translation_jobs.id UNIQUE | |
| `user_id` | UUID FK → users.id | |
| `filename` | TEXT NOT NULL | |
| `source_lang` | VARCHAR(8) | |
| `target_lang` | VARCHAR(8) | |
| `char_count` | INT | |
| `created_at` | TIMESTAMP | DEFAULT now() |

### 6.2 Внутренние Pydantic-модели (не в БД)

**`BlockType`** (Enum):
```
HEADING | PARAGRAPH | TABLE | LIST_ITEM | CODE
```

**`Block`**:
```python
class Block(BaseModel):
    type: BlockType
    text: str
    level: int = 0          # для заголовков H1-H6
    bbox: tuple | None      # для PDF (x0, y0, x1, y1)
    font_size: float | None # для PDF
    is_bold: bool = False
    raw_html: str | None    # для HTML-блоков
```

**`ParsedDocument`**:
```python
class ParsedDocument(BaseModel):
    format: Literal["pdf", "html", "txt"]
    blocks: list[Block]
    metadata: dict[str, Any] = {}
```

**`Chunk`**:
```python
class Chunk(BaseModel):
    index: int
    blocks: list[Block]
    text: str               # склеенный текст для LLM
    overlap_prev: str = ""  # перекрытие с предыдущим чанком
```

---

## 7. REST API

### 7.1 Переводы

| Метод | Путь | Описание |
|---|---|---|
| `POST` | `/api/translations/upload` | Загрузка файла, создание job |
| `GET` | `/api/translations/{job_id}/status` | Статус и прогресс |
| `GET` | `/api/translations/{job_id}/download` | Скачать результат |
| `DELETE` | `/api/translations/{job_id}` | Отменить задачу |

**`POST /api/translations/upload`**
- Body: `multipart/form-data` — `file`, `target_lang: str`, `user_id: UUID`
- Response: `{"job_id": "...", "status": "pending"}`

**`GET /api/translations/{job_id}/status`**
```json
{
  "job_id": "...",
  "status": "running",
  "source_lang": "en",
  "target_lang": "ru",
  "chunk_done": 3,
  "chunk_total": 10,
  "error_msg": null
}
```

**`GET /api/translations/{job_id}/download`**
- Response: `FileResponse` с оригинальным форматом
- 404 если `status != done`

**`DELETE /api/translations/{job_id}`**
- Отмена: pending → удаляет из очереди; running → выставляет флаг
- Response: `{"status": "cancelled"}`

### 7.2 Глоссарий

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/api/glossary?user_id={uuid}` | Список терминов |
| `POST` | `/api/glossary` | Добавить термин |
| `PUT` | `/api/glossary/{id}` | Изменить термин |
| `DELETE` | `/api/glossary/{id}` | Удалить термин |

### 7.3 История

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/api/history?user_id={uuid}&limit=20&offset=0` | Список истории |
| `DELETE` | `/api/history/{id}` | Удалить запись + файлы |

### 7.4 UI страницы (Jinja2)

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/` | Главная, форма загрузки |
| `GET` | `/history` | Таблица истории |
| `GET` | `/glossary` | Управление глоссарием |

---

## 8. LangGraph агент

### 8.1 State

```python
class TranslationState(TypedDict):
    job_id: str
    user_id: str
    target_lang: str
    source_lang: str                  # заполняется нодой DetectLanguage
    input_path: str
    parsed_doc: ParsedDocument        # заполняется нодой ParseDocument
    chunks: list[Chunk]               # заполняется нодой ChunkDocument
    current_chunk_idx: int            # счётчик итерации
    translated_chunks: list[str]      # накапливается
    context_summary: str              # краткое резюме предыдущих чанков
    glossary: dict[str, str]          # загружается один раз
    result_path: str                  # заполняется нодой AssembleDocument
    cancelled: bool                   # флаг отмены
    error: str | None
```

### 8.2 Граф (ноды и переходы)

```
START
  └─► parse_document
        └─► detect_language
              └─► load_glossary
                    └─► chunk_document
                          └─► [cancelled?] ──yes──► END (cancelled)
                                │ no
                                ▼
                          translate_chunk
                                └─► update_context
                                      └─► update_job_progress
                                            └─► [more chunks?] ──yes──► [cancelled?]
                                                      │ no
                                                      ▼
                                              assemble_document
                                                    └─► save_history
                                                          └─► END (done)
```

### 8.3 Описание нод

| Нода | Вход | Выход | Действие |
|---|---|---|---|
| `parse_document` | `input_path` | `parsed_doc` | Парсит файл по формату |
| `detect_language` | `parsed_doc` | `source_lang` | LLM/langdetect на первые 1000 символов |
| `load_glossary` | `user_id` | `glossary` | SELECT из БД |
| `chunk_document` | `parsed_doc` | `chunks`, `chunk_total` | Разбивка на чанки |
| `translate_chunk` | `chunks[idx]`, `glossary`, `context_summary` | `translated_chunks[+1]` | LLM-вызов с промптом |
| `update_context` | последний `translated_chunks[-1]` | `context_summary` | LLM: ключевые термины + резюме |
| `update_job_progress` | `current_chunk_idx` | `current_chunk_idx+1` | UPDATE jobs SET chunk_done |
| `assemble_document` | `translated_chunks`, `parsed_doc` | `result_path` | Сборка итогового файла |
| `save_history` | `result_path` | — | UPDATE jobs + INSERT history |

### 8.4 Промпт перевода (translate_chunk)

```
Ты профессиональный переводчик с {source_lang} на {target_lang}.

Глоссарий (используй точно, не изменяй):
{glossary_formatted}

Контекст предыдущих переводов:
{context_summary}

Переведи следующий текст, сохраняя структуру, форматирование и стиль.
Переводи только текст, не добавляй комментариев:

{chunk_text}
```

### 8.5 LangChain Tools

```python
@tool
async def lookup_glossary(term: str, user_id: str) -> str:
    """Найти перевод термина в глоссарии пользователя."""

@tool
def detect_language_tool(text: str) -> str:
    """Определить язык текста. Возвращает ISO 639-1 код (ru, en, de, ...)."""
```

---

## 9. Парсинг документов

### 9.1 PDF (`pdfplumber`)

- Для каждой страницы: `page.extract_text_lines()` + `page.extract_tables()`
- Классификация блока по `font_size`:
  - `>= 18` → `HEADING level=1`
  - `>= 14` → `HEADING level=2`
  - `>= 12` → `HEADING level=3`
  - иначе → `PARAGRAPH`
- Таблицы → `TABLE` с текстом ячеек
- Сохраняются `bbox`, `font_size`, `is_bold` для последующей сборки

### 9.2 HTML (`BeautifulSoup4`)

- Обход дерева тегов:
  - `h1-h6` → `HEADING(level=1-6, raw_html=str(tag))`
  - `p` → `PARAGRAPH(raw_html=str(tag))`
  - `li` → `LIST_ITEM(raw_html=str(tag))`
  - `table` → `TABLE(raw_html=str(tag))`
- `text = tag.get_text(strip=True)`

### 9.3 TXT

- Разбивка по `\n\n`
- Каждый непустой фрагмент → `PARAGRAPH`

---

## 10. Чанкинг

```python
def chunk_document(
    doc: ParsedDocument,
    max_tokens: int = 2000,
    overlap_tokens: int = 200,
) -> list[Chunk]:
    ...
```

- Токены считаются приблизительно: `len(text.split()) * 1.3`
- Один `Block` не разрывается посередине
- Последние `overlap_tokens` токенов предыдущего чанка → `overlap_prev` следующего

---

## 11. Сборка документов

```python
async def assemble_document(
    original: ParsedDocument,
    translated_chunks: list[str],
    output_path: str,
) -> str:
    ...
```

### TXT
- `"\n\n".join(translated_chunks)` → `.txt`

### HTML
- Клонирует оригинальный BeautifulSoup-объект
- Заменяет `NavigableString` текстовых узлов на переведённые строки (по порядку)
- `soup.prettify()` → `.html`

### PDF
- Для каждого `Block` берёт переведённый текст
- Рисует через `reportlab.platypus`:
  - Размер шрифта из `block.font_size`
  - Жирный из `block.is_bold`
  - Таблицы через `reportlab.platypus.Table`
- Результат → `.pdf`

---

## 12. Асинхронная очередь задач

```python
class TranslationQueue:
    _queue: asyncio.Queue[str]           # содержит job_id
    _running: dict[str, asyncio.Task]
    _cancel_flags: dict[str, bool]

    async def enqueue(self, job_id: str) -> None: ...
    async def cancel(self, job_id: str) -> None: ...
    async def _worker(self) -> None: ...   # бесконечный цикл
    async def _run_job(self, job_id: str) -> None: ...
```

- `QUEUE_WORKERS` воркеров запускаются в `lifespan` FastAPI
- Каждый воркер: `job_id = await queue.get()` → загружает job из БД → строит state → запускает граф
- Флаг отмены проверяется нодой `update_job_progress` перед каждым следующим чанком

---

## 13. Конфигурация (`app/config.py`)

```python
class Settings(BaseSettings):
    # LLM
    llm_provider: Literal["gigachat", "ollama"] = "gigachat"
    gigachat_api_key: str = ""
    gigachat_model: str = "GigaChat-Pro"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"

    # Database
    database_url: str

    # Files
    upload_dir: str = "./uploads"
    result_dir: str = "./results"
    max_file_size_mb: int = 50

    # Queue
    queue_workers: int = 2

    model_config = SettingsConfigDict(env_file=".env")
```

**`.env.example`**:
```dotenv
LLM_PROVIDER=gigachat
GIGACHAT_API_KEY=your_key_here
GIGACHAT_MODEL=GigaChat-Pro
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_doc_translator
UPLOAD_DIR=./uploads
RESULT_DIR=./results
MAX_FILE_SIZE_MB=50
QUEUE_WORKERS=2
```

---

## 14. Веб-интерфейс

### Главная (`index.html`)
- Drag-and-drop зона для файлов PDF / TXT / HTML
- Dropdown выбора языка назначения (~20 языков)
- После загрузки — карточка с прогресс-баром:
  - Фазы: `Определение языка → Разбивка → Перевод N/M → Сборка`
  - Polling `/api/translations/{job_id}/status` каждые 2 секунды
- Кнопка скачивания при `status=done`
- Кнопка отмены при `status=pending|running`

### История (`history.html`)
- Таблица: файл, языки, дата, статус, размер
- Действия: скачать, удалить

### Глоссарий (`glossary.html`)
- Таблица терминов с inline-редактированием
- Форма добавления нового термина
- Кнопка удаления каждого термина

### Навигация (`base.html`)
- Хедер: Перевод / История / Глоссарий
- Минималистичный дизайн (CSS без фреймворков)

---

## 15. Тесты

| Файл | Тесты |
|---|---|
| `tests/conftest.py` | Фикстуры: test БД, mock LLM, тестовые файлы |
| `tests/test_parsers.py` | Парсинг PDF/HTML/TXT, классификация блоков |
| `tests/test_chunker.py` | Лимит токенов, перекрытие, целостность блоков |
| `tests/test_agent_nodes.py` | Каждая нода с mock LLM и готовым state |
| `tests/test_assembler.py` | Сборка TXT/HTML/PDF из переведённых чанков |
| `tests/test_api.py` | Upload / status / download / glossary CRUD / history |

Ключевые тест-кейсы:
- `test_translate_chunk_uses_glossary` — глоссарий инжектируется в промпт
- `test_translate_chunk_uses_context` — контекст передаётся между чанками
- `test_chunk_no_block_split` — блоки не разрываются на границе чанков
- `test_download_pending_job_returns_404` — нельзя скачать незавершённый job

---

## 16. Порядок реализации

| # | Этап | Файлы |
|---|---|---|
| 1 | Конфигурация | `app/config.py`, `.env.example` |
| 2 | БД + миграции | `app/models/`, `alembic/` |
| 3 | Парсеры документов | `app/services/document_parser.py` |
| 4 | Чанкер | `app/services/document_chunker.py` |
| 5 | LangGraph агент (mock LLM) | `app/agent/` |
| 6 | LLM интеграция | `app/agent/nodes/translate_chunk.py`, `app/agent/nodes/detect_language.py` |
| 7 | Очередь задач | `app/services/task_queue.py` |
| 8 | FastAPI роутеры | `app/api/`, `app/main.py` |
| 9 | Сборщик документов | `app/services/document_assembler.py` |
| 10 | UI | `app/templates/`, `app/static/` |
| 11 | Тесты | `tests/` |
