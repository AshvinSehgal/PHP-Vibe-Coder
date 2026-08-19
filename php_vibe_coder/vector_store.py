import os
import re
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import chromadb
from sentence_transformers import SentenceTransformer

class VectorStore:
    def __init__(self, root):
        self.database_directory = Path(root) / "storage" / "vector_database"
        self.collection_name = "php_docs"
        self.embedding_model_name = "sentence-transformers/all-MiniLM-L6-v2"
        self.client = None
        self.collection = None
        self.model = None

    def load(self):
        if self.collection is not None:
            return
        if not self.database_directory.exists():
            raise ValueError("The documentation index has not been built. Run scripts/build_document_index.py first.")
        self.client = chromadb.PersistentClient(path=str(self.database_directory))
        self.collection = self.client.get_collection(self.collection_name)
        self.model = SentenceTransformer(
            self.embedding_model_name,
            device="cpu",
            local_files_only=True,
        )

    def search(self, prompt, limit=5):
        self.load()
        query_embedding = self.model.encode(
            [prompt],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        result_count = self.collection.count()
        result = self.collection.query(
            query_embeddings=query_embedding.tolist(),
            n_results=min(30, result_count),
            include=["documents", "metadatas", "distances"],
        )
        documents = result["documents"][0]
        metadatas = result["metadatas"][0]
        distances = result["distances"][0]
        guidance = self.collection.query(
            query_embeddings=query_embedding.tolist(),
            n_results=min(10, result_count),
            where={"category": "project-guidance"},
            include=["documents", "metadatas", "distances"],
        )
        documents.extend(guidance["documents"][0])
        metadatas.extend(guidance["metadatas"][0])
        distances.extend(guidance["distances"][0])
        query_words = self.words(prompt)
        matches = []
        seen = set()
        for document, metadata, distance in zip(documents, metadatas, distances):
            source = metadata.get("source", "documentation")
            chunk = "guidance" if metadata.get("category") == "project-guidance" else metadata.get("chunk")
            source_key = (source, chunk)
            if source_key in seen:
                continue
            seen.add(source_key)
            keyword_matches = len(query_words & self.words(document))
            semantic_score = 1 / (1 + distance)
            keyword_score = keyword_matches / max(1, len(query_words))
            guidance_score = 0.3 if metadata.get("category") == "project-guidance" else 0
            matches.append({
                "source": source,
                "text": document,
                "score": semantic_score + keyword_score + guidance_score,
                "category": metadata.get("category", "unknown"),
            })
        matches.sort(key=lambda item: item["score"], reverse=True)
        return matches[:limit]

    def words(self, text):
        return set(re.findall(r"[a-zA-Z][a-zA-Z0-9_]+", text.lower()))
