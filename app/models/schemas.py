"""Pydantic v2 schemas for internal data structures and API request/response."""

import enum
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Internal document models (not stored in DB)
# ---------------------------------------------------------------------------


class BlockType(str, enum.Enum):
    """Semantic type of a document block."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST_ITEM = "list_item"
    CODE = "code"
    FORM_FIELD = "form_field"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    HEADER = "header"


class Block(BaseModel):
    """Single structural element extracted from a document."""

    type: BlockType
    text: str
    level: int = 0
    bbox: tuple[float, float, float, float] | None = None
    font_size: float | None = None
    font_color: str | None = None  # hex "#RRGGBB"
    is_bold: bool = False
    font_name: str | None = None
    line_height: float | None = None
    style_runs: list[dict[str, Any]] | None = None
    table_cells: list[list[str]] | None = None
    layout_label: str | None = None
    non_translatable: bool = False
    raw_html: str | None = None
    page_index: int | None = None


class ParsedDocument(BaseModel):
    """Result of parsing an uploaded file into structured blocks."""

    format: Literal["pdf", "html", "txt"]
    blocks: list[Block]
    metadata: dict[str, Any] = {}


class LayoutBlock(BaseModel):
    """Layout-aware block used by the PDF rebuild pipeline."""

    block_id: str
    page: int
    type: BlockType | str
    bbox: tuple[float, float, float, float]
    text: str = ""
    style: dict[str, Any] = {}
    reading_order: int = 0
    column_id: int = 0
    non_translatable: bool = False
    table_cells: list[list[str]] | None = None


class PageLayout(BaseModel):
    """Layout container for one page."""

    page: int
    width: float
    height: float
    blocks: list[LayoutBlock]


class DocumentLayoutIR(BaseModel):
    """Document-level layout IR used for translation/rebuild mapping."""

    pages: list[PageLayout]


class Chunk(BaseModel):
    """A segment of blocks sized to fit an LLM context window."""

    index: int
    blocks: list[Block]
    text: str
    overlap_prev: str = ""


# ---------------------------------------------------------------------------
# API request / response schemas
# ---------------------------------------------------------------------------


class UploadResponse(BaseModel):
    """Returned after a file is uploaded and a job is enqueued."""

    job_id: uuid.UUID
    status: str


class JobStatusResponse(BaseModel):
    """Current state of a translation job."""

    job_id: uuid.UUID
    status: str
    source_lang: str | None = None
    target_lang: str
    chunk_done: int = 0
    chunk_total: int | None = None
    error_msg: str | None = None


class CancelResponse(BaseModel):
    """Returned when a job is successfully cancelled."""

    status: str


class GlossaryEntryCreate(BaseModel):
    """Body for creating a new glossary entry."""

    user_id: uuid.UUID
    source_term: str
    target_term: str


class GlossaryEntryUpdate(BaseModel):
    """Body for updating an existing glossary entry."""

    source_term: str
    target_term: str


class GlossaryEntryResponse(BaseModel):
    """Single glossary entry returned by the API."""

    id: uuid.UUID
    user_id: uuid.UUID
    source_term: str
    target_term: str
    created_at: datetime

    model_config = {"from_attributes": True}


class HistoryItemResponse(BaseModel):
    """Single history record returned by the API."""

    id: uuid.UUID
    job_id: uuid.UUID
    user_id: uuid.UUID
    filename: str
    source_lang: str | None = None
    target_lang: str | None = None
    char_count: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
