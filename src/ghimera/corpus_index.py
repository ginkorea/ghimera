"""Optional compiled ANN index; durable source/vector truth remains in SQLite."""

from importlib.metadata import version

from ghimera.corpus_config import CorpusConfig
from ghimera.embedding_types import unit_vector


class NativePassageIndex:
    def __init__(self, config: CorpusConfig, vectors: tuple[tuple[float, ...], ...]) -> None:
        # Lazy imports: installing/using the collector never acquires this extra.
        import faiss
        import numpy as np

        if version("faiss-cpu") != "1.15.1":
            raise ValueError("native corpus search requires its pinned faiss-cpu extra")
        if any(len(row) != config.encoder.dimensions for row in vectors):
            raise ValueError("stored vectors must match the corpus embedding space")
        index = faiss.IndexHNSWFlat(
            config.encoder.dimensions, config.hnsw_neighbors, faiss.METRIC_INNER_PRODUCT
        )
        index.hnsw.efConstruction = config.hnsw_construction
        index.hnsw.efSearch = config.hnsw_search
        if vectors:
            index.add(np.asarray([unit_vector(row) for row in vectors], dtype=np.float32))
        self._index, self._dimensions = index, config.encoder.dimensions

    def search(
        self, vector: tuple[float, ...], *, candidates: int
    ) -> tuple[tuple[int, float], ...]:
        import numpy as np

        if len(vector) != self._dimensions or candidates <= 0:
            raise ValueError("query vector and candidate count must match the index")
        if self._index.ntotal == 0:
            return ()
        scores, indices = self._index.search(
            np.asarray([unit_vector(vector)], dtype=np.float32),
            min(candidates, self._index.ntotal),
        )
        return tuple(
            (int(index), max(-1.0, min(1.0, float(score))))
            for index, score in zip(indices[0], scores[0], strict=True)
            if index >= 0
        )
