"""Paper & Ember styling for the Streamlit frontend.

Colours and fonts are set in .streamlit/config.toml. This module adds what the
theme file can't express: tightly tracked serif headings, eyebrows, pastel
cards, chat bubbles, agent chips and the footer.
"""

from __future__ import annotations

from html import escape

import streamlit as st

_CSS = """
<style>
:root{
  --paper:#E8E3D2; --paper-soft:#EDE8D7; --surface:#FAF4E8; --panel:#E4DDCC;
  --inset:#DED9C6; --rule:#D3CEBB;
  --ink:#14130C; --ink-body:#4F4938; --ink-muted:#5F594A;
  --ember:#C55644; --ember-deep:#A14C3F;
  --sage:#B5CAB1; --lilac:#C1BCD6; --peach:#E9C0AA; --on-pastel-body:#36311F;
  --font-serif: Newsreader, Georgia, "Times New Roman", serif;
  --font-sans: Inter, "Helvetica Neue", Helvetica, Arial, sans-serif;
  --font-mono: "JetBrains Mono", Menlo, Consolas, monospace;
}

/* Headings: serif, regular weight, tight tracking */
h1, h2, h3, h4 { font-family: var(--font-serif) !important; font-weight: 400 !important;
                 letter-spacing: -0.02em; line-height: 1.05; color: var(--ink); }

.pe-eyebrow { font: 600 0.8125rem/1.2 var(--font-sans); letter-spacing: 0.15em;
              text-transform: uppercase; color: var(--ember-deep); margin: 0 0 12px; }
.pe-title { font: 400 3rem/1.0 var(--font-serif); letter-spacing: -0.03em; color: var(--ink); margin: 0; }
.pe-title .pe-ember { color: var(--ember); }
.pe-heading { font: 400 2.25rem/1.05 var(--font-serif); letter-spacing: -0.02em; color: var(--ink); margin: 0 0 8px; }
.pe-sub { font: 400 1rem/1.4 var(--font-sans); color: var(--ink-body); max-width: 46rem; margin: 16px 0 24px; }

/* Pastel card trio: separated by colour, no borders or shadows */
.pe-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 32px; margin: 32px 0; }
.pe-card { border-radius: 24px; padding: 32px; min-height: 240px; display: flex; flex-direction: column;
           justify-content: space-between; color: var(--ink); }
.pe-card--sage { background: var(--sage); } .pe-card--lilac { background: var(--lilac); }
.pe-card--peach { background: var(--peach); }
.pe-card svg { width: 44px; height: 44px; stroke: var(--ink); stroke-width: 2; fill: none;
               stroke-linecap: round; stroke-linejoin: round; }
.pe-card h4 { font: 500 1.5rem/1.1 var(--font-serif) !important; letter-spacing: -0.02em; margin: 0 0 8px; }
.pe-card p { font: 400 1rem/1.4 var(--font-sans); color: var(--on-pastel-body); margin: 0 0 12px; }
.pe-card code { font: 400 0.8125rem/1.4 var(--font-mono); background: transparent; color: var(--on-pastel-body);
                padding: 0; }
@media (max-width: 900px) { .pe-cards { grid-template-columns: 1fr; } .pe-card { min-height: 0; gap: 24px; } }

/* Chat: assistant answers sit on raised paper */
[data-testid="stChatMessage"] { border-radius: 16px; padding: 16px 20px; background: transparent; }
/* (custom avatars hide the role in test ids, so answers are recognised by their agent chip) */
[data-testid="stChatMessage"]:has(.pe-chip) { background: var(--surface); }
/* Headings inside answers stay at card-title size or smaller */
[data-testid="stChatMessage"] h1, [data-testid="stChatMessage"] h2 { font-size: 1.5rem !important; margin-top: 8px; }
[data-testid="stChatMessage"] h3, [data-testid="stChatMessage"] h4 { font-size: 1.25rem !important; }

/* Agent chip (record-card style mono chip) */
.pe-chip { display: inline-block; font: 400 0.8125rem/1.4 var(--font-mono); background: var(--inset);
           color: var(--ink-body); border-radius: 8px; padding: 2px 10px; margin-top: 8px; }

/* Tabs: sans labels, hairline underneath */
[data-testid="stTabs"] [role="tab"] p { font-family: var(--font-sans); font-weight: 500; }

/* Footer */
.pe-footer { display: flex; justify-content: space-between; border-top: 1px solid var(--rule);
             margin-top: 64px; padding-top: 16px; font: 400 0.875rem/1.35 var(--font-sans); color: var(--ink-muted); }
</style>
"""

# Thin line icons (2px stroke, round caps), one per card.
ICONS = {
    "document": '<svg viewBox="0 0 24 24"><path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4"/>'
    '<path d="M9 12h6M9 16h6"/></svg>',
    "scales": '<svg viewBox="0 0 24 24"><path d="M12 3v18M7 21h10M4 7h16"/>'
    '<path d="M6 7l-3 7a3 3 0 0 0 6 0zM18 7l-3 7a3 3 0 0 0 6 0z"/></svg>',
    "pen": '<svg viewBox="0 0 24 24"><path d="M4 20l4-1L19 8l-3-3L5 16z"/><path d="M14 7l3 3"/></svg>',
}


def apply_theme() -> None:
    """Inject the Paper & Ember component styles (call once per run)."""
    st.markdown(_CSS, unsafe_allow_html=True)


def eyebrow(text: str) -> None:
    """Small uppercase kicker, e.g. 'UPLOAD A DOCUMENT'."""
    st.markdown(f'<p class="pe-eyebrow">{escape(text)}</p>', unsafe_allow_html=True)


def section_heading(kicker: str, title: str) -> None:
    """Eyebrow plus serif heading, top-left — used at the top of each tab."""
    st.markdown(
        f'<p class="pe-eyebrow">{escape(kicker)}</p><h2 class="pe-heading">{escape(title)}</h2>',
        unsafe_allow_html=True,
    )


def page_header(kicker: str, title: str, ember_line: str, subtitle: str) -> None:
    """Cover-style header: eyebrow, serif title whose last line is in ember, subtitle."""
    st.markdown(
        f'<p class="pe-eyebrow">{escape(kicker)}</p>'
        f'<h1 class="pe-title">{escape(title)}<br><span class="pe-ember">{escape(ember_line)}</span></h1>'
        f'<p class="pe-sub">{escape(subtitle)}</p>',
        unsafe_allow_html=True,
    )


def card_trio(cards: list[tuple[str, str, str, str]]) -> None:
    """Three pastel cards (sage → lilac → peach). Each card: (icon, title, body, mono line)."""
    colours = ["sage", "lilac", "peach"]
    html = "".join(
        f'<div class="pe-card pe-card--{colour}">{ICONS[icon]}'
        f"<div><h4>{escape(title)}</h4><p>{escape(body)}</p><code>{escape(mono)}</code></div></div>"
        for colour, (icon, title, body, mono) in zip(colours, cards, strict=False)
    )
    st.markdown(f'<div class="pe-cards">{html}</div>', unsafe_allow_html=True)


def agent_chip(agent_name: str) -> None:
    """Mono chip naming the agent that answered."""
    st.markdown(f'<span class="pe-chip">agent · {escape(agent_name)}</span>', unsafe_allow_html=True)


def footer(left: str, right: str) -> None:
    """Hairline footer: name on the left, product on the right."""
    st.markdown(
        f'<div class="pe-footer"><span>{escape(left)}</span><span>{escape(right)}</span></div>',
        unsafe_allow_html=True,
    )
