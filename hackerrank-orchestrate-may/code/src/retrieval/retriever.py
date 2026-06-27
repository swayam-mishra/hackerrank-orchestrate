from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder

from src.config import RETRIEVAL_TOP_K_BM25, RETRIEVAL_TOP_K_FINAL

_CHUNK_SIZE = 500
_CHUNK_OVERLAP = 100


def _company_from_path(file_path: str) -> str:
    for part in Path(file_path).parts:
        if part.lower() == "hackerrank":
            return "HackerRank"
        if part.lower() == "claude":
            return "Claude"
        if part.lower() == "visa":
            return "Visa"
    return "Unknown"


def _chunk_text(text: str) -> list:
    chunks = []
    start = 0
    while start < len(text):
        chunk = text[start : start + _CHUNK_SIZE]
        if chunk.strip():
            chunks.append(chunk)
        start += _CHUNK_SIZE - _CHUNK_OVERLAP
    return chunks


class Retriever:
    def __init__(self, data_dir: str):
        self.chunks = []
        self._load_corpus(Path(data_dir))
        tokenized = [c["text"].lower().split() for c in self.chunks]
        self.index = BM25Okapi(tokenized)
        self.cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")

    def _load_corpus(self, data_path: Path):
        for file_path in data_path.rglob("*"):
            if file_path.suffix not in (".md", ".txt") or not file_path.is_file():
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            company = _company_from_path(str(file_path))
            for chunk in _chunk_text(text):
                self.chunks.append({
                    "text": chunk,
                    "source_file": str(file_path),
                    "company": company,
                })

    def retrieve(self, query: str, company: str = None, top_k: int = RETRIEVAL_TOP_K_BM25):
        scores = self.index.get_scores(query.lower().split()).copy()

        if company:
            for i, chunk in enumerate(self.chunks):
                if chunk["company"].lower() == company.lower():
                    scores[i] *= 1.5

        top_indices = np.argsort(scores)[::-1][:top_k]
        top_chunks = [self.chunks[i] for i in top_indices]
        top_score = float(scores[top_indices[0]]) if len(top_indices) > 0 else 0.0

        return top_chunks, top_score

    def rerank(self, query: str, chunks: list, top_k: int = RETRIEVAL_TOP_K_FINAL):
        pairs = [[query, c["text"]] for c in chunks]
        scores = self.cross_encoder.predict(pairs)
        order = np.argsort(scores)[::-1]
        top_indices = order[:top_k]
        top_chunks = [chunks[i] for i in top_indices]
        top_score = float(scores[top_indices[0]]) if len(top_indices) > 0 else 0.0
        # Full ranked score list (descending) for confidence quantification
        all_scores = [float(scores[i]) for i in order]
        return top_chunks, top_score, all_scores
