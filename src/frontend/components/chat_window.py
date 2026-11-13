"""Chat window component for the Streamlit frontend."""

from __future__ import annotations

import streamlit as st

from frontend.components.theme import agent_chip

# Line-style Material icons instead of emoji avatars.
AVATARS = {"user": ":material/person:", "assistant": ":material/balance:"}


def render_citations(citations: list[dict]) -> None:
    """Show the sources behind an answer in a collapsible list."""
    if not citations:
        return
    with st.expander(f"{len(citations)} source(s)"):
        for citation in citations:
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"**[{citation['index']}]** {citation.get('document_title', '')}")
                st.caption(citation.get("chunk_content", "")[:300])
            with col2:
                if citation.get("page_number"):
                    st.caption(f"Page {citation['page_number']}")
                if citation.get("relevance_score"):
                    st.caption(f"Score {citation['relevance_score']:.2f}")


# What the user is told when an answer isn't verified against their documents.
GROUNDING_NOTES = {
    "not_grounded": "Not fully supported by your documents: some statements could not be found in the sources. "
    "Check them before relying on this answer.",
    "unverified": "This answer could not be checked against your documents.",
    "no_documents": "No matching passages were found in your documents; this answer is based on general knowledge.",
}


def render_grounding(grounding: str | None) -> None:
    """Show a notice when the answer is not verified against the user's documents."""
    note = GROUNDING_NOTES.get(grounding or "")
    if note:
        st.warning(note, icon=":material/warning:")


def render_message(role: str, content: str, **kwargs) -> None:
    """Render a single chat message with optional metadata.

    Args:
        role: Message role ('user' or 'assistant').
        content: Message text content.
        **kwargs: Optional metadata (citations, agent_name, grounding).
    """
    with st.chat_message(role, avatar=AVATARS.get(role)):
        st.markdown(content)
        render_grounding(kwargs.get("grounding"))
        render_citations(kwargs.get("citations", []))
        if kwargs.get("agent_name"):
            agent_chip(kwargs["agent_name"])
