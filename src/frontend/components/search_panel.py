"""Search panel component for direct knowledge base search."""

from __future__ import annotations

import httpx
import streamlit as st

from frontend.components.theme import section_heading


def render_search_panel(api_base_url: str) -> None:
    """Render a search panel for querying the legal knowledge base.

    Args:
        api_base_url: Base URL of the LegalMind AI API.
    """
    section_heading("Knowledge base", "Search every indexed document")

    col1, col2 = st.columns([3, 1])

    with col1:
        query = st.text_input("Search query", placeholder="e.g., aansprakelijkheid bij arbeidsongeval")

    with col2:
        doc_type = st.selectbox(
            "Document type",
            options=["All", "contract", "correspondence", "case_file", "policy"],
        )

    col3, col4 = st.columns(2)
    with col3:
        use_rewriting = st.checkbox("Query rewriting", value=True)
    with col4:
        use_semantic = st.checkbox("Semantic ranking", value=True)

    top_k = st.slider("Number of results", min_value=1, max_value=20, value=5)

    if query and st.button("Search", type="primary"):
        try:
            request_body = {
                "query": query,
                "top": top_k,
                "use_query_rewriting": use_rewriting,
                "use_semantic_ranking": use_semantic,
            }
            if doc_type != "All":
                request_body["document_type"] = doc_type

            response = httpx.post(
                f"{api_base_url}/api/search",
                json=request_body,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            st.caption(f"Query used: _{data['query_used']}_")
            st.caption(f"Results: {data['total_count']}")

            for result in data["results"]:
                score_text = f"Score: {result['score']:.3f}"
                if result.get("reranker_score"):
                    score_text += f" | Reranker: {result['reranker_score']:.3f}"

                with st.expander(f"{result.get('title', 'Untitled')} — {result['document_type']} ({score_text})"):
                    st.markdown(result["content"])

                    if result.get("entities"):
                        st.markdown(f"**Entities:** {', '.join(result['entities'][:10])}")
                    if result.get("key_phrases"):
                        st.markdown(f"**Key phrases:** {', '.join(result['key_phrases'][:10])}")
                    if result.get("page_number"):
                        st.caption(f"Page: {result['page_number']}")

        except httpx.ConnectError:
            st.warning("Cannot connect to API. Is the backend running?")
        except Exception as e:
            st.error(f"Search error: {e}")
