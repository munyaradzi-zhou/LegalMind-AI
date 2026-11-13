"""Document viewer component for inspecting uploaded documents."""

from __future__ import annotations

import httpx
import streamlit as st

from frontend.components.theme import section_heading


def render_document_viewer(api_base_url: str) -> None:
    """Render a document viewer panel showing indexed documents.

    Args:
        api_base_url: Base URL of the LegalMind AI API.
    """
    section_heading("Document viewer", "Look inside your documents")

    # Search within documents
    query = st.text_input("Search documents", placeholder="Enter search term...")

    if query:
        try:
            response = httpx.post(
                f"{api_base_url}/api/search",
                json={"query": query, "top": 5, "use_query_rewriting": False},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            if data["results"]:
                for result in data["results"]:
                    with st.expander(f"{result['title']} — {result['document_type']} (Score: {result['score']:.3f})"):
                        st.markdown(result["content"])
                        if result.get("entities"):
                            st.caption(f"Entities: {', '.join(result['entities'][:5])}")
                        if result.get("key_phrases"):
                            st.caption(f"Key phrases: {', '.join(result['key_phrases'][:5])}")
            else:
                st.info("No results found.")

        except httpx.ConnectError:
            st.warning("API not available.")
        except Exception as e:
            st.error(f"Search error: {e}")
