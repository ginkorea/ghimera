"""Narrow faiss-cpu 1.15.1 operations used by the native cosine index."""
import numpy as np
from numpy.typing import NDArray

METRIC_INNER_PRODUCT: int

class HNSW:
    efConstruction: int
    efSearch: int

class IndexHNSWFlat:
    def __init__(self, d: int, M: int, metric: int) -> None: ...
    hnsw: HNSW
    ntotal: int
    d: int
    def add(self, values: NDArray[np.float32]) -> None: ...
    def search(self, values: NDArray[np.float32], k: int) -> tuple[NDArray[np.float32], NDArray[np.int64]]: ...
