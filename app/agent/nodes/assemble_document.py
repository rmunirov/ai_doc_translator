"""Assemble document node — merges translated chunks into output file."""

import logging
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.agent.state import TranslationState
from app.services.document_assembler import assemble_document

logger = logging.getLogger(__name__)


async def assemble_document_node(state: TranslationState) -> dict[str, Any]:
    """Assemble translated chunks into the output document file.

    Delegates format-specific logic (TXT, HTML, PDF) to the
    ``document_assembler`` service. The result path is stored back in state.

    Args:
        state: Current translation pipeline state.

    Returns:
        dict with ``result_path`` key set to the written output file path.

    Raises:
        ValueError: If required state fields are missing.
    """
    job_id = state.get("job_id", "unknown")
    parsed_doc = state.get("parsed_doc")
    chunks = state.chunks
    translated_chunks = state.translated_chunks
    input_path = state.get("input_path", "")

    if not parsed_doc or not translated_chunks:
        raise ValueError("parsed_doc and translated_chunks required")

    settings = get_settings()
    result_dir = Path(settings.result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(input_path).suffix
    output_path = str(result_dir / f"{job_id}{ext}")

    source_path = input_path if parsed_doc.format == "pdf" else None
    assembly_warnings: list[str] = []
    await assemble_document(
        parsed_doc,
        chunks,
        translated_chunks,
        output_path,
        source_path=source_path,
        assembly_warnings=assembly_warnings,
    )

    logger.info(
        "Assembled document",
        extra={"job_id": job_id, "output_path": output_path},
    )
    warning = "; ".join(assembly_warnings).strip()
    if warning:
        logger.warning(
            "Assembled with warning",
            extra={"job_id": job_id, "warning": warning},
        )
    return {"result_path": output_path, "assembly_warning": warning}
