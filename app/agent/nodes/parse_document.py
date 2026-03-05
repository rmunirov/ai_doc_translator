"""Parse document node — extracts blocks from uploaded file."""

import logging
from typing import Any

from app.agent.state import TranslationState
from app.services.document_parser import DocumentParser

logger = logging.getLogger(__name__)


async def parse_document_node(state: TranslationState) -> dict[str, Any]:
    """Parse the input file and populate parsed_doc."""
    job_id = state.get("job_id", "unknown")
    input_path = state.get("input_path", "")
    if not input_path:
        raise ValueError("input_path is required")
    try:
        parser = DocumentParser()
        parsed_doc = await parser.parse(input_path)
        logger.info(
            "Parsed document",
            extra={"job_id": job_id, "blocks": len(parsed_doc.blocks)},
        )
        return {"parsed_doc": parsed_doc}
    except Exception as exc:
        logger.error(
            "parse_document_node failed",
            extra={"job_id": job_id, "error": str(exc)},
        )
        raise
