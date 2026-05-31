import streamlit as st

DARK_CSS = """
<style>
.stApp { background-color: #0f0f14; color: #fafafa; }
.stSuccess { background-color: rgba(16, 185, 129, 0.2); }
.stError { background-color: rgba(239, 68, 68, 0.2); }
#MainMenu, footer, [data-testid="stToolbar"] { visibility: hidden; }
</style>
"""

LIGHT_CSS = """
<style>
.stApp { background-color: #f8fafc; color: #1e293b; }
.stSuccess { background-color: rgba(16, 185, 129, 0.15); }
.stError { background-color: rgba(239, 68, 68, 0.15); }
#MainMenu, footer, [data-testid="stToolbar"] { visibility: hidden; }
</style>
"""


def apply_theme():
    """Apply theme based on session state"""
    theme = st.session_state.get('theme', 'dark')
    st.markdown(DARK_CSS if theme == 'dark' else LIGHT_CSS, unsafe_allow_html=True)
