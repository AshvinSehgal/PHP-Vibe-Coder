# Simple architecture

The project has one short pipeline:

```text
User prompt
    ↓
Keyword feature detection
    ↓
Word-overlap retrieval from local PHP notes
    ↓
Plain PHP template generation
    ↓
Streamlit code preview and ZIP download
```

`app.py` contains the user interface. It sends the prompt to `SimplePHPAgent` and displays the returned result.

`php_vibe_coder/simple_agent.py` contains the complete coding agent. `find_features` recognizes common requirements, `retrieve` finds relevant local notes, and `generate_files` creates five understandable PHP project files.

The generated project uses plain PHP, PDO and MySQL. This avoids frameworks, model servers, containers and other advanced infrastructure so each line can be explained using basic Python and web-development concepts.
