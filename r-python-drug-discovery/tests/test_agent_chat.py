"""Exercise the optional chat page's controls without network requests."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


STREAMLIT_ROOT = Path(__file__).resolve().parents[1] / "streamlit"
sys.path.insert(0, str(STREAMLIT_ROOT))
import _shared  # noqa: E402


class FakeSession:
    def __init__(self):
        self.requests = []

    def sql(self, query, params):
        assert query == "SELECT SNOWFLAKE.CORTEX.DATA_AGENT_RUN(?, ?, TRUE) AS RESPONSE"
        self.requests.append(json.loads(params[1]))
        return self

    def collect(self):
        return [{"RESPONSE": json.dumps({
            "status": "completed",
            "metadata": {"thread_id": 27, "assistant_message_id": 30 + len(self.requests)},
            "content": [{"type": "text", "text": f"Answer {len(self.requests)}"}],
        })}]


def test_chat_retains_thread_and_resets_on_new_conversation():
    session = FakeSession()
    with patch.object(_shared, "init_session", return_value=session):
        app = AppTest.from_file(str(STREAMLIT_ROOT / "pages" / "ask_agent.py")).run()
        assert not app.exception
        app.chat_input[0].set_value("How many active?").run()
        assert not app.exception
        app.chat_input[0].set_value("And inactive?").run()
        assert not app.exception
        assert len(app.session_state["agent_messages"]) == 4
        assert session.requests[1]["thread_id"] == 27
        assert session.requests[1]["parent_message_id"] == 31
        app.button[0].click().run()
        assert not app.exception
        assert app.session_state["agent_messages"] == []
        assert "agent_thread_id" not in app.session_state
