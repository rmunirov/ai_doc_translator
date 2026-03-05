"""FastAPI application entry point — lifespan, router mounting, static files."""

from pathlib import Path

from dotenv import load_dotenv

# Load .env into os.environ for LangSmith and libs that read env directly.
# Pydantic Settings reads .env for its own fields but does not populate os.environ.
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.glossaries import router as glossary_router
from app.api.history import router as history_router
from app.api.translations import router as translations_router
from app.config import get_settings
from app.models.database import engine
from app.services.task_queue import translation_queue

logger = logging.getLogger(__name__)

_SHUTDOWN_TIMEOUT = 10.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start background workers on startup; cancel them on shutdown."""
    settings = get_settings()
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.result_dir).mkdir(parents=True, exist_ok=True)

    await translation_queue.startup()

    workers = [
        asyncio.create_task(translation_queue._worker())
        for _ in range(settings.queue_workers)
    ]

    yield

    await translation_queue.shutdown()
    for w in workers:
        w.cancel()
    try:
        await asyncio.wait_for(
            asyncio.gather(*workers, return_exceptions=True),
            timeout=_SHUTDOWN_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Workers did not finish within %s s, proceeding with shutdown",
            _SHUTDOWN_TIMEOUT,
        )
    await engine.dispose()


app = FastAPI(
    title="AI Doc Translator",
    description="Translate PDF, TXT and HTML documents using LLM.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(translations_router, prefix="/api/translations", tags=["translations"])
app.include_router(glossary_router, prefix="/api/glossary", tags=["glossary"])
app.include_router(history_router, prefix="/api/history", tags=["history"])

_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def page_index(request: Request) -> HTMLResponse:
    """Render the main upload page."""
    return templates.TemplateResponse(request, "index.html")


@app.get("/history", response_class=HTMLResponse, include_in_schema=False)
async def page_history(request: Request) -> HTMLResponse:
    """Render the translation history page."""
    return templates.TemplateResponse(request, "history.html")


@app.get("/glossary", response_class=HTMLResponse, include_in_schema=False)
async def page_glossary(request: Request) -> HTMLResponse:
    """Render the glossary management page."""
    return templates.TemplateResponse(request, "glossary.html")
