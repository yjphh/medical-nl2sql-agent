from __future__ import annotations

import time
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from frontend.ui import result

# Injected once by ``app.py``; scrolling is additive so the chat keeps working
# when the browser refuses cross-frame access.
FLASH_CSS = """
<style>
@keyframes msgFlash {
  0%   { box-shadow: 0 0 0 6px rgba(255, 193, 7, 0.45); border-radius: 8px; }
  100% { box-shadow: 0 0 0 0 rgba(255, 193, 7, 0); }
}
.msg-flash { animation: msgFlash 1.6s ease-out; }
</style>
"""

# The chat sits in its own scroll pane (Feishu's right pane) so the composer
# stays visible while the transcript scrolls.
CHAT_CSS = """
<style>
.st-key-chat_scroll {
  /* Streamlit makes the block a flex item (flex: 1 1 0%); opt out so the
     viewport-based height is actually used. */
  flex: 0 0 auto;
  height: calc(100vh - 24rem);
  min-height: 22rem;
  overflow-y: auto;
  padding-right: 6px;
}
/* The two right panes scroll themselves; the page must not scroll as a whole
   (otherwise the wheel at the far right drags the title under the header).
   The lock is applied by ``_FIT_JS`` after the pane is sized: keeping it in
   CSS would clip the composer on a very short window if the script cannot run. */
</style>
"""

# Measures the real remaining height and pins the transcript pane to it, so the
# composer stays on screen at any window size/zoom without a page scrollbar.
# ``__TICK__`` is replaced on every run so the iframe reloads and re-measures
# after layout changes (Streamlit skips identical component HTML).
_FIT_JS = """
<script>
<!-- __TICK__ -->
setTimeout(() => {
  const p = window.parent;
  if (!p.__nl2sqlFit) {
    p.__nl2sqlFit = new p.Function(`
      var main = document.querySelector('[data-testid="stMain"]');
      var box = document.querySelector('.st-key-chat_scroll');
      if (!main || !box) return;
      main.scrollTop = 0;
      var input = document.querySelector('[data-testid="stChatInput"]');
      var inputBox = input ? (input.closest('[data-testid="stElementContainer"]') || input) : null;
      var mainTop = main.getBoundingClientRect().top;
      var top = box.getBoundingClientRect().top - mainTop;
      var reserved = inputBox ? inputBox.getBoundingClientRect().height + 24 : 96;
      var avail = main.clientHeight - top - reserved;
      // On a very short window the title/composer alone fill the viewport;
      // leave the page scrollable there instead of clipping the composer.
      if (avail < 160) { main.style.overflowY = 'auto'; return; }
      box.style.height = avail + 'px';
      box.style.minHeight = '0px';
      main.style.overflowY = 'hidden';
    `);
    p.addEventListener('resize', p.__nl2sqlFit);
  }
  p.__nl2sqlFit();
}, 150);
</script>
"""

_SCROLL_JS = """
<script>
setTimeout(() => {{
  const doc = window.parent.document;
  const el = doc.querySelector('.st-key-msg_{mid}');
  if (!el) return;
  el.scrollIntoView({{behavior: 'smooth', block: 'start'}});
  el.classList.add('msg-flash');
  setTimeout(() => el.classList.remove('msg-flash'), 1600);
}}, 120);
</script>
"""

_BOTTOM_JS = """
<script>
setTimeout(() => {
  const box = window.parent.document.querySelector('.st-key-chat_scroll');
  if (!box) return;
  box.scrollTop = box.scrollHeight;
}, 120);
</script>
"""


def render_messages(messages: list[dict[str, Any]], show_trace: bool = False) -> None:
    for message in messages:
        # The container key becomes the ``st-key-msg_<mid>`` class the scroll
        # helper looks for.
        with st.container(key=_container_key(message)):
            _render_message(message, show_trace)


def apply_scroll(mid: str | None, *, to_bottom: bool = False) -> None:
    """Jump to one message, otherwise keep a growing transcript in view."""
    if mid:
        components.html(_SCROLL_JS.format(mid=mid), height=0)
    elif to_bottom:
        scroll_to_bottom()


def scroll_to_bottom() -> None:
    """Pin the transcript to its last line (e.g. the in-flight status)."""
    components.html(_BOTTOM_JS, height=0)


def fit_chat_pane() -> None:
    """Pin the transcript pane to the height left over in the viewport."""
    tick = f"{time.monotonic():.6f}"
    components.html(_FIT_JS.replace("__TICK__", tick), height=0)


def _container_key(message: dict[str, Any]) -> str:
    mid = message.get("mid") or f"legacy{id(message)}"
    return f"msg_{mid}"


def _render_message(message: dict[str, Any], show_trace: bool) -> None:
    role = message.get("role", "assistant")
    with st.chat_message(role):
        if message.get("kind") == "error":
            st.error(message.get("content", "发生错误"))
            # Raw exception text is for debugging, not for the user.
            if message.get("technical"):
                with st.expander("技术详情"):
                    st.code(str(message["technical"]), language="text")
            return
        if message.get("content"):
            st.markdown(message["content"])
        if message.get("sql"):
            result.render_sql_card(message, show_trace=show_trace)
