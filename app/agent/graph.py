"""Translation LangGraph — defines nodes and edges."""

from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.state import TranslationState
from app.agent.nodes.assemble_document import assemble_document_node
from app.agent.nodes.chunk_document import chunk_document_node
from app.agent.nodes.detect_language import detect_language_node
from app.agent.nodes.load_glossary import load_glossary_node
from app.agent.nodes.parse_document import parse_document_node
from app.agent.nodes.save_history import save_history_node
from app.agent.nodes.translate_chunk import translate_chunk_node
from app.agent.nodes.update_context import update_context_node
from app.agent.nodes.update_job_progress import update_job_progress_node


def _route_after_chunk(state: TranslationState) -> str:
    """Route after chunk_document: END if cancelled, else translate_chunk."""
    if state.get("cancelled"):
        return "end"
    return "translate_chunk"


def _route_after_progress(state: TranslationState) -> str:
    """Route after update_job_progress: loop or assemble."""
    current = state.get("current_chunk_idx", 0)
    chunks = state.get("chunks", [])
    if current < len(chunks):
        if state.get("cancelled"):
            return "end"
        return "translate_chunk"
    return "assemble_document"


def build_translation_graph() -> CompiledStateGraph[TranslationState, Any, Any, Any]:
    """Build and return the compiled translation graph."""
    graph: StateGraph[TranslationState] = StateGraph(TranslationState)

    graph.add_node("parse_document", parse_document_node)
    graph.add_node("detect_language", detect_language_node)
    graph.add_node("load_glossary", load_glossary_node)
    graph.add_node("chunk_document", chunk_document_node)
    graph.add_node("translate_chunk", translate_chunk_node)
    graph.add_node("update_context", update_context_node)
    graph.add_node("update_job_progress", update_job_progress_node)
    graph.add_node("assemble_document", assemble_document_node)
    graph.add_node("save_history", save_history_node)

    graph.set_entry_point("parse_document")
    graph.add_edge("parse_document", "detect_language")
    graph.add_edge("detect_language", "load_glossary")
    graph.add_edge("load_glossary", "chunk_document")
    graph.add_conditional_edges(
        "chunk_document",
        _route_after_chunk,
        {"translate_chunk": "translate_chunk", "end": END},
    )
    graph.add_edge("translate_chunk", "update_context")
    graph.add_edge("update_context", "update_job_progress")
    graph.add_conditional_edges(
        "update_job_progress",
        _route_after_progress,
        {
            "translate_chunk": "translate_chunk",
            "assemble_document": "assemble_document",
            "end": END,
        },
    )
    graph.add_edge("assemble_document", "save_history")
    graph.add_edge("save_history", END)

    return graph.compile()
