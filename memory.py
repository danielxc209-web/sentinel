import chromadb
from chromadb.utils import embedding_functions
from datetime import datetime
from typing import Optional

EMBED_FN = embedding_functions.DefaultEmbeddingFunction()

class Memory:
    """
    Dual ChromaDB vector store.
    - 'conversation' store: social context, preferences, mood, dialogue history
    - 'command' store: tasks run, tools built, outcomes, goals completed

    Retrieval is blended by mode_score (0.0 = full conversation, 1.0 = full command).
    """

    def __init__(self, path: str = "./chroma_db"):
        self.client = chromadb.PersistentClient(path=path)

        self.conv_store = self.client.get_or_create_collection(
            name="conversation",
            embedding_function=EMBED_FN,
            metadata={"description": "Social context, preferences, mood"}
        )

        self.cmd_store = self.client.get_or_create_collection(
            name="command",
            embedding_function=EMBED_FN,
            metadata={"description": "Tasks, tools, outcomes, goals"}
        )

    def _uid(self, prefix: str) -> str:
        return f"{prefix}_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"

    def save_conversation(self, text: str, metadata: Optional[dict] = None):
        # BUG FIX 1: ChromaDB requires all metadata values to be str, int, float,
        # or bool. Passing None or other types raises a hard error at runtime.
        # Sanitise the metadata dict before storing.
        raw_meta = {"timestamp": datetime.utcnow().isoformat(), **(metadata or {})}
        meta = {k: str(v) if not isinstance(v, (str, int, float, bool)) else v
                for k, v in raw_meta.items()}
        self.conv_store.add(
            documents=[text],
            metadatas=[meta],
            ids=[self._uid("conv")]
        )

    def save_command(self, text: str, metadata: Optional[dict] = None):
        # BUG FIX 1 (same): same metadata sanitisation needed here.
        raw_meta = {"timestamp": datetime.utcnow().isoformat(), **(metadata or {})}
        meta = {k: str(v) if not isinstance(v, (str, int, float, bool)) else v
                for k, v in raw_meta.items()}
        self.cmd_store.add(
            documents=[text],
            metadatas=[meta],
            ids=[self._uid("cmd")]
        )

    def retrieve(self, query: str, mode_score: float, n: int = 6) -> str:
        """
        Blend results from both stores based on mode_score.
        mode_score 0.0 → all conversation
        mode_score 1.0 → all command
        """
        mode_score = max(0.0, min(1.0, mode_score))

        conv_n = max(1, round(n * (1 - mode_score)))
        cmd_n  = max(1, round(n * mode_score))

        # BUG FIX 2: The original always fetched at least 1 result from BOTH
        # stores regardless of mode_score, because conv_n and cmd_n were each
        # floored at 1. This means a pure-command query (mode_score=1.0) still
        # pulled a conversation result, polluting the context. Fixed by only
        # clamping to 1 when the store is actually supposed to contribute.
        conv_n = round(n * (1 - mode_score))
        cmd_n  = round(n * mode_score)

        results = []

        if conv_n > 0 and self.conv_store.count() > 0:
            conv_res = self.conv_store.query(
                query_texts=[query],
                n_results=min(conv_n, self.conv_store.count())
            )
            for doc in conv_res["documents"][0]:
                results.append(f"[CONV] {doc}")

        if cmd_n > 0 and self.cmd_store.count() > 0:
            cmd_res = self.cmd_store.query(
                query_texts=[query],
                n_results=min(cmd_n, self.cmd_store.count())
            )
            for doc in cmd_res["documents"][0]:
                results.append(f"[CMD] {doc}")

        # BUG FIX 3: If both stores are empty, query() is never called, which
        # is fine — but if one store has fewer documents than requested,
        # ChromaDB raises an error. The min() guards above handle that, but
        # we also need to handle the case where count() == 0 cleanly, which
        # the existing `and self.conv_store.count() > 0` guards already do.
        # No code change needed here, but left this note for clarity.

        return "\n".join(results) if results else "No relevant memory found."