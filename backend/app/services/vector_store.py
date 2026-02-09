from __future__ import annotations

import json
import uuid
from typing import Optional, List, Dict

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import settings


_client: Optional[chromadb.ClientAPI] = None


def get_chroma_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=settings.CHROMA_PERSIST_DIR,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
    return _client


def get_collection():
    client = get_chroma_client()
    return client.get_or_create_collection(
        name="machine_states",
        metadata={"hnsw:space": "cosine"},
    )


def store_machine_state(drawing_id: uuid.UUID, machine_state: dict):
    collection = get_collection()
    doc_text = json.dumps(machine_state, default=str)
    collection.upsert(
        ids=[str(drawing_id)],
        documents=[doc_text],
        metadatas=[{"drawing_id": str(drawing_id)}],
    )


def search_similar(query_text: str, n_results: int = 5) -> List[Dict]:
    collection = get_collection()
    results = collection.query(query_texts=[query_text], n_results=n_results)
    return [
        {"drawing_id": meta["drawing_id"], "document": doc}
        for meta, doc in zip(results["metadatas"][0], results["documents"][0])
    ]
