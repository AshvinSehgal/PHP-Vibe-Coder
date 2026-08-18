from hashlib import sha256
import os
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import chromadb
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer

root = Path(__file__).resolve().parents[1]
docs_directory = root / "docs"
database_directory = root / "storage" / "vector_database"
collection_name = "php_docs"
embedding_model_name = "sentence-transformers/all-MiniLM-L6-v2"

def read_document(path):
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() in (".html", ".htm"):
        soup = BeautifulSoup(text, "html.parser")
        for unwanted in soup.find_all(("script", "style", "nav", "header", "footer")):
            unwanted.decompose()
        content = soup.find("main")
        if content is None:
            content = soup.find("article")
        if content is None:
            content = soup.body
        if content is None:
            content = soup
        return content.get_text("\n", strip=True)
    return text

def split_into_chunks(text):
    paragraphs = [paragraph.strip() for paragraph in text.splitlines() if paragraph.strip()]
    chunks = []
    current = []
    current_length = 0
    for paragraph in paragraphs:
        if current and current_length + len(paragraph) > 1600:
            chunk = "\n".join(current)
            chunks.append(chunk)
            overlap = chunk[-250:]
            current = [overlap, paragraph]
            current_length = len(overlap) + len(paragraph)
        else:
            current.append(paragraph)
            current_length += len(paragraph)
    if current:
        chunks.append("\n".join(current))
    return [chunk for chunk in chunks if len(chunk) >= 100]

def batches(items, size):
    for position in range(0, len(items), size):
        yield items[position:position + size]

if __name__ == "__main__":
    allowed_extensions = {".html", ".htm", ".md", ".txt"}
    documents = []
    metadatas = []
    identifiers = []
    for path in docs_directory.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in allowed_extensions:
            continue
        source = str(path.relative_to(docs_directory))
        text = read_document(path)
        for number, chunk in enumerate(split_into_chunks(text)):
            identifier_text = f"{source}:{number}:{chunk}"
            identifier = sha256(identifier_text.encode("utf-8")).hexdigest()
            documents.append(chunk)
            identifiers.append(identifier)
            metadatas.append({
                "source": source,
                "category": path.parts[-2],
                "chunk": number,
            })
    if not documents:
        raise SystemExit("No doc files were found in the docs folder.")
    model = SentenceTransformer(
        embedding_model_name,
        device="cpu",
        local_files_only=True,
    )
    client = chromadb.PersistentClient(path=str(database_directory))
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass
    collection = client.create_collection(collection_name)
    for positions in batches(list(range(len(documents))), 64):
        batch_documents = [documents[position] for position in positions]
        batch_metadatas = [metadatas[position] for position in positions]
        batch_identifiers = [identifiers[position] for position in positions]
        embeddings = model.encode(batch_documents, normalize_embeddings=True, show_progress_bar=False)
        collection.add(
            ids=batch_identifiers,
            documents=batch_documents,
            metadatas=batch_metadatas,
            embeddings=embeddings.tolist(),
        )
    print(f"Indexed {len(documents)} doc chunks.")
    print(f"Vector database: {database_directory}")
