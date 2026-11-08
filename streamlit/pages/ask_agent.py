"""Optional Cortex Agent chat over measured activity and the R predictor."""

import json

import streamlit as st

from _shared import _DB_SCHEMA, init_session


AGENT = f"{_DB_SCHEMA}.AROMATASE_AGENT"


def ask(question):
    payload = {"messages": [{"role": "user", "content": [{"type": "text", "text": question}]}]}
    if st.session_state.get("agent_thread_id"):
        payload["thread_id"] = st.session_state.agent_thread_id
        payload["parent_message_id"] = st.session_state.agent_parent_message_id
    response = init_session().sql(
        "SELECT SNOWFLAKE.CORTEX.DATA_AGENT_RUN(?, ?, TRUE) AS RESPONSE",
        params=[AGENT, json.dumps(payload)],
    ).collect()[0]["RESPONSE"]
    result = json.loads(response) if isinstance(response, str) else response
    if result.get("status") != "completed":
        raise RuntimeError(f"Agent returned {result.get('status', 'no status')}")
    metadata = result.get("metadata", {})
    st.session_state.agent_thread_id = metadata["thread_id"]
    st.session_state.agent_parent_message_id = metadata["assistant_message_id"]
    answer = "\n\n".join(item["text"] for item in result.get("content", []) if item.get("type") == "text")
    if not answer:
        raise RuntimeError("Agent returned no text response")
    return answer


st.title("Ask an Agent")
st.caption("Explore measured aromatase data, assay context, predictions, and molecule triage. Research demonstration only.")
st.info("The model's quantile ranges are not calibrated confidence intervals. Check measured values before acting on predictions.")

if "agent_messages" not in st.session_state:
    st.session_state.agent_messages = []

if st.button("New conversation"):
    st.session_state.agent_messages = []
    st.session_state.pop("agent_thread_id", None)
    st.session_state.pop("agent_parent_message_id", None)
    st.rerun()

for message in st.session_state.agent_messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if question := st.chat_input("Ask about aromatase compounds or paste a SMILES"):
    st.session_state.agent_messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Checking the data and model..."):
            try:
                answer = ask(question)
            except Exception as error:
                st.error(f"Agent request failed: {error}")
            else:
                st.markdown(answer)
                st.session_state.agent_messages.append({"role": "assistant", "content": answer})
