"""LegalMind AI — Streamlit Frontend.

Three tabs: Chat (with sources), Search (direct knowledge base search), and
Documents (uploaded documents, and limiting the chat to selected ones).

Usage: streamlit run src/frontend/app.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import streamlit as st

# `streamlit run src/frontend/app.py` puts src/frontend on sys.path, not src/,
# so add src/ to be able to import the `frontend.components` package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from frontend.components.chat_window import AVATARS, render_citations, render_grounding, render_message
from frontend.components.document_viewer import render_document_viewer
from frontend.components.search_panel import render_search_panel
from frontend.components.theme import agent_chip, apply_theme, card_trio, eyebrow, footer, page_header, section_heading

if TYPE_CHECKING:
    from collections.abc import Iterator

API_BASE_URL = os.getenv("LEGALMIND_API_URL", "http://localhost:8000")
DOCUMENT_TYPES = ["contract", "correspondence", "case_file", "policy"]

st.set_page_config(
    page_title="LegalMind AI",
    page_icon=":material/balance:",
    layout="wide",
)


def init_session_state() -> None:
    """Initialize Streamlit session state (it survives Streamlit's reruns)."""
    # None until the API creates a session; the API's session_id is then reused.
    st.session_state.setdefault("session_id", None)
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("uploaded_docs", [])
    st.session_state.setdefault("use_streaming", False)
    st.session_state.setdefault("selected_doc_ids", [])


# ─── Sidebar ─────────────────────────────────────────────────────────────


def render_sidebar() -> None:
    """Render the sidebar with document upload and settings."""
    with st.sidebar:
        eyebrow("LegalMind AI")
        st.markdown("### Case assistant")
        st.divider()

        eyebrow("Upload a document")
        uploaded_file = st.file_uploader(
            "Legal document",
            type=["pdf", "docx", "png", "jpg", "jpeg", "tiff", "txt"],
        )
        doc_type = st.selectbox("Document type", DOCUMENT_TYPES, key="upload_doc_type")
        title = st.text_input("Title (optional)", key="upload_title")

        if uploaded_file and st.button("Process document", type="primary", use_container_width=True):
            upload_document(uploaded_file, doc_type, title)

        st.divider()
        eyebrow("Conversation")
        if st.button("New conversation", use_container_width=True):
            st.session_state.session_id = None
            st.session_state.messages = []
            st.rerun()
        st.session_state.use_streaming = st.checkbox(
            "Stream answers word by word", value=st.session_state.use_streaming
        )
        if st.session_state.session_id:
            st.caption(f"Session `{st.session_state.session_id[:8]}`")


def upload_document(uploaded_file, doc_type: str, title: str) -> None:
    """Send a file to the ingestion endpoint and remember the result."""
    with st.spinner("Uploading, analysing and indexing..."):
        try:
            data = {"document_type": doc_type, "language": "nl"}
            if title:
                data["title"] = title
            response = httpx.post(
                f"{API_BASE_URL}/api/ingest",
                files={"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)},
                data=data,
                timeout=300.0,
            )
            response.raise_for_status()
            result = response.json()
        except httpx.ConnectError:
            st.error("Cannot connect to the LegalMind AI API. Is the backend running?")
            return
        except httpx.HTTPStatusError as e:
            st.error(f"Upload failed ({e.response.status_code}): {e.response.text[:300]}")
            return

    st.session_state.uploaded_docs.append(
        {
            "name": title or uploaded_file.name,
            "document_id": result["document_id"],
            "document_type": doc_type,
            "chunk_count": result["chunk_count"],
            "status": result.get("status", "completed"),
        }
    )
    if result.get("status") == "partial":
        st.warning(f"Processed with problems: only part of the {result['chunk_count']} chunks were indexed.")
    else:
        st.success(f"Document processed: {result['chunk_count']} chunks indexed.")


# ─── Chat tab ────────────────────────────────────────────────────────────


def render_chat_tab() -> None:
    """Render the chat: history, input, and the assistant's answer."""
    selected = st.session_state.selected_doc_ids
    if selected:
        st.info(f"Answers are limited to {len(selected)} selected document(s). Change this in the Documents tab.")

    # Elements appear in the order they are created, so reserve the slots above the
    # input box first and fill them afterwards; new messages then land above the input.
    cards_slot = st.empty()
    conversation = st.container()
    prompt = st.chat_input("Ask a legal question... (e.g., 'Wat zijn de opzegvoorwaarden?')")

    if not st.session_state.messages and not prompt:
        with cards_slot:
            card_trio(
                [
                    ("document", "Analyse a contract", "Summaries, clauses and risks.", "Vat dit contract samen"),
                    ("scales", "Research the law", "Statutes and case law, with sources.", "Wat zegt art. 7:271 BW?"),
                    (
                        "pen",
                        "Draft a letter",
                        "Formal Dutch correspondence and memos.",
                        "Schrijf een ingebrekestelling",
                    ),
                ]
            )

    with conversation:
        for msg in st.session_state.messages:
            render_message(
                msg["role"],
                msg["content"],
                citations=msg.get("citations", []),
                agent_name=msg.get("agent_name"),
                grounding=msg.get("grounding"),
            )
        if prompt:
            send_message(prompt, selected)


def send_message(prompt: str, selected: list[str]) -> None:
    """Show the question, ask the API, show and store the answer."""
    st.session_state.messages.append({"role": "user", "content": prompt})
    render_message("user", prompt)

    request_body = {
        "message": prompt,
        "session_id": st.session_state.session_id,
        "user_id": "streamlit-user",
        "stream": st.session_state.use_streaming,
        "document_ids": selected or None,
    }

    try:
        if st.session_state.use_streaming:
            reply = stream_reply(request_body)
        else:
            with st.spinner("Analyzing..."):
                response = httpx.post(f"{API_BASE_URL}/api/chat", json=request_body, timeout=180.0)
                response.raise_for_status()
                reply = response.json()
            render_message(
                "assistant",
                reply["message"],
                citations=reply.get("citations", []),
                agent_name=reply.get("agent_name"),
                grounding=reply.get("grounding"),
            )
    except httpx.ConnectError:
        st.error("Cannot connect to the LegalMind AI API. Is the backend running?")
        return
    except httpx.HTTPError as e:
        st.error(f"Error: {e}")
        return

    # Reuse the API's session id so the next message continues this conversation.
    st.session_state.session_id = reply.get("session_id") or st.session_state.session_id
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": reply["message"],
            "citations": reply.get("citations", []),
            "agent_name": reply.get("agent_name", ""),
            "grounding": reply.get("grounding"),
        }
    )


def stream_reply(request_body: dict) -> dict:
    """Show the answer as Server-Sent Events arrive; return the full reply and its metadata."""
    final: dict = {}

    def chunks() -> Iterator[str]:
        with httpx.stream("POST", f"{API_BASE_URL}/api/chat", json=request_body, timeout=180.0) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line.removeprefix("data:").strip())
                if event.get("done"):
                    final.update(event)
                elif event.get("content"):
                    yield event["content"]

    with st.chat_message("assistant", avatar=AVATARS["assistant"]):
        text = st.write_stream(chunks())
        citations = final.get("citations", [])
        render_grounding(final.get("grounding"))
        render_citations(citations)
        if final.get("agent_name"):
            agent_chip(final["agent_name"])

    return {
        "message": text if isinstance(text, str) else "".join(text),
        "session_id": final.get("session_id"),
        "agent_name": final.get("agent_name", ""),
        "grounding": final.get("grounding"),
        "citations": citations,
    }


# ─── Documents tab ───────────────────────────────────────────────────────


def render_documents_tab() -> None:
    """List uploaded documents, let the user scope the chat to some of them, and search inside them."""
    section_heading("Your documents", "Processed in this session")
    docs = st.session_state.uploaded_docs
    if not docs:
        st.caption("No documents uploaded in this session yet. Use the sidebar to upload one.")
    else:
        st.dataframe(
            [
                {"Name": d["name"], "Type": d["document_type"], "Chunks": d["chunk_count"], "Status": d["status"]}
                for d in docs
            ],
            hide_index=True,
            use_container_width=True,
        )
        names = {d["document_id"]: d["name"] for d in docs}
        st.session_state.selected_doc_ids = st.multiselect(
            "Ask only about these documents (leave empty to search everything)",
            options=list(names),
            default=[i for i in st.session_state.selected_doc_ids if i in names],
            format_func=names.get,
        )

    st.divider()
    render_document_viewer(API_BASE_URL)


def main() -> None:
    """Main entry point."""
    init_session_state()
    apply_theme()
    render_sidebar()

    page_header(
        "LegalMind AI · Legal case assistant",
        "Ask your case files,",
        "get grounded answers.",
        "Dutch legal questions answered by specialist agents for analysis, research, drafting "
        "and compliance, with sources from your own documents.",
    )

    chat_tab, search_tab, documents_tab = st.tabs(["Chat", "Search", "Documents"])
    with chat_tab:
        render_chat_tab()
    with search_tab:
        render_search_panel(API_BASE_URL)
    with documents_tab:
        render_documents_tab()

    footer("LegalMind AI", "")


if __name__ == "__main__":
    main()
