import os
import uuid
from datetime import datetime
from typing import Optional

from qdrant_client import QdrantClient, models

# Remember to change these to enviornment variables. You can still push because it's just me and Javan.
QDRANT_URL    = "https://9b642903-8f06-4739-9996-cef41db2b93b.us-east4-0.gcp.cloud.qdrant.io"
QDRANT_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIn0.dgtszI6cj9EEcrzryCwKFkUx6RV7JSD_sp9BHthmAbI"


EMBED_MODEL = "BAAI/bge-small-en-v1.5"
VECTOR_SIZE = 384
CONV_COL    = "conversation"
CMD_COL     = "command"

class Memory:
    def __init__(self):
        self.client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
        # Load fastembed locally for generating embeddings
        from fastembed import TextEmbedding
        self.embedder = TextEmbedding(model_name=EMBED_MODEL)
        self._ensure_collections()

    def _embed(self, text: str) -> list[float]:
        return list(self.embedder.embed([text]))[0].tolist()

    def _ensure_collections(self):
        existing = {c.name for c in self.client.get_collections().collections}
        for name in (CONV_COL, CMD_COL):
            if name not in existing:
                self.client.create_collection(
                    collection_name=name,
                    vectors_config=models.VectorParams(
                        size=VECTOR_SIZE,
                        distance=models.Distance.COSINE
                    )
                )

    def _now(self) -> str:
        return datetime.utcnow().isoformat()

    def _save(self, collection: str, text: str, metadata: Optional[dict]):
        vector = self._embed(text)
        self.client.upsert(
            collection_name=collection,
            points=[models.PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={"text": text, "timestamp": self._now(), **(metadata or {})}
            )]
        )

    def save_conversation(self, text: str, metadata: Optional[dict] = None):
        self._save(CONV_COL, text, metadata)

    def save_command(self, text: str, metadata: Optional[dict] = None):
        self._save(CMD_COL, text, metadata)

    def retrieve(self, query: str, mode_score: float, n: int = 6) -> str:
        mode_score = max(0.0, min(1.0, mode_score))
        conv_n = max(1, round(n * (1 - mode_score)))
        cmd_n  = max(1, round(n * mode_score))
        vector = self._embed(query)
        results = []

        try:
            hits = self.client.search(
                collection_name=CONV_COL,
                query_vector=vector,
                limit=conv_n
            )
            for h in hits:
                results.append(f"[CONV] {h.payload.get('text', '')}")
        except Exception as e:
            print(f"[Memory] Conv query error: {e}")

        try:
            hits = self.client.search(
                collection_name=CMD_COL,
                query_vector=vector,
                limit=cmd_n
            )
            for h in hits:
                results.append(f"[CMD] {h.payload.get('text', '')}")
        except Exception as e:
            print(f"[Memory] Cmd query error: {e}")

        return "\n".join(results) if results else "No relevant memory found."