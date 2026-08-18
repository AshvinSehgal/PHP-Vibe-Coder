from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from php_vibe_coder.simple_agent import SimplePHPAgent
from php_vibe_coder.llm import LocalLLM
from php_vibe_coder.runner import CodeIgniterPreviewServer
from php_vibe_coder.vector_store import VectorStore
try:
    import streamlit as st
    import streamlit.components.v1 as components
except ImportError as exc:
    raise SystemExit("Install Streamlit with: python3 -m pip install -e .") from exc

st.set_page_config(page_title="Simple PHP Vibe Coder", page_icon="💻", layout="wide")
st.title("Simple PHP Vibe Coder")
st.caption("Prompt → understand the request → retrieve PHP notes → generate code")

@st.cache_resource
def load_llm():
    return LocalLLM()

@st.cache_resource
def load_vector_store():
    return VectorStore(Path(__file__).parent)

@st.cache_resource
def load_preview_server():
    return CodeIgniterPreviewServer()

llm = load_llm()
vector_store = load_vector_store()
preview_server = load_preview_server()

default_prompt = "Create a customer registration system with login, a MySQL database and a simple admin page."
prompt = st.text_area("Describe the PHP application", default_prompt, height=140)

if "result" not in st.session_state:
    st.session_state.result = None

if st.button("Generate PHP project", type="primary"):
    if not prompt.strip():
        st.warning("Please describe the application first.")
    else:
        st.session_state.result = None
        preview_server.stop()
        agent = SimplePHPAgent(Path(__file__).parent, llm, vector_store)
        with st.status("Building CodeIgniter Project") as status:
            try:
                st.session_state.result = agent.build(prompt)
                if st.session_state.result["status"] == "working":
                    preview = preview_server.start(st.session_state.result["workspace"])
                    st.session_state.result["preview_url"] = preview["url"]
                    st.session_state.result["preview_error"] = preview["error"]
                status.update(label="Build complete", state="complete")
            except Exception as error:
                status.update(label="Build failed", state="error")
                st.exception(error)

result = st.session_state.result
if result:
    message = (f"Generated {len(result['files'])} files in {result['workspace']}")
    if result["status"] == "working":
        st.success(message)
    elif result["status"] == "environment_error":
        st.warning(message + ". The code was generated, but the local environment needs attention.")
    else:
        st.error(message + ". The agent could not correct every error.")
    overview_tab, preview_tab, code_tab, knowledge_tab, run_tab = st.tabs(("Overview", "Preview", "Generated code", "Retrieved knowledge", "Run output"))
    with overview_tab:
        st.subheader("What the agent understood")
        st.write("Status:", result["status"])
        st.write("Summary:", result["summary"])
        st.write("Features:")
        for feature in result["features"]:
            st.write(f"- {feature}")
    with preview_tab:
        preview_url = result.get("preview_url")
        preview_error = result.get("preview_error")
        if preview_url:
            st.caption("Live preview of the generated CodeIgniter application")
            st.link_button("Open preview in a new browser tab", preview_url)
            components.iframe(preview_url, height=650, scrolling=True)
        elif preview_error:
            st.error(preview_error)
        else:
            st.info("A preview is available after a project reaches working status.")
    with code_tab:
        selected_file = st.selectbox("Choose a file", list(result["files"]))
        language = selected_file.rsplit(".", 1)[-1]
        st.code(result["files"][selected_file], language=language)
        archive = BytesIO()
        project_path = Path(result["workspace"])
        excluded_names = {"vendor", ".git", ".env", "logs", "cache"}
        with ZipFile(archive, "w", ZIP_DEFLATED) as zip_file:
            for path in project_path.rglob("*"):
                if not path.is_file():
                    continue
                relative = path.relative_to(project_path)
                if any(part in excluded_names for part in relative.parts):
                    continue
                zip_file.write(path, relative)
        st.download_button("Download project as ZIP", archive.getvalue(), "php-project.zip", "application/zip")
    with knowledge_tab:
        if result["knowledge"]:
            for item in result["knowledge"]:
                st.subheader(item["source"])
                st.write(item["text"])
        else:
            st.write("No matching knowledge note was found.")
    with run_tab:
        st.write("Final status:", result["status"])
        if result["status"] == "working":
            st.success("CodeIgniter started and the webpage responded successfully.")
        elif result["status"] == "environment_error":
            st.warning("The local environment prevented the application from running.")
        else:
            st.error("The application still contains errors after the correction attempts.")
        if result["errors"]:
            for number, error in enumerate(result["errors"], start=1):
                st.subheader(f"Error {number}: {error.get('kind', 'unknown')}")
                st.code(error["output"])
        st.write("Correction attempts:", len(result["attempts"]))
