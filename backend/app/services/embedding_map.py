"""Squashes a project's chunk embeddings (hundreds of dimensions each) down to three, so the search page can..."""
from typing import Any, List, Sequence, Tuple

import numpy as np

MAX_POINTS = 2000


def as_vector(value: Any) -> np.ndarray:
    """One stored embedding as a float array."""
    if isinstance(value, str):
        value = [float(part) for part in value.strip("[] ").split(",") if part.strip()]
    return np.asarray(value, dtype=np.float64)


def project_3d(vectors: Sequence[Any]) -> Tuple[np.ndarray, List[float]]:
    """PCA to three dimensions."""
    n = len(vectors)
    if n == 0:
        return np.zeros((0, 3)), [0.0, 0.0, 0.0]

    matrix = np.vstack([as_vector(v) for v in vectors])
    centred = matrix - matrix.mean(axis=0)

    _, singular, vt = np.linalg.svd(centred, full_matrices=False)
    k = min(3, vt.shape[0])
    coords = np.zeros((n, 3))
    coords[:, :k] = centred @ vt[:k].T

    largest = np.abs(coords).max()
    if largest > 0:
        coords /= largest

    variance = singular**2
    total = variance.sum()
    explained = [float(variance[i] / total) if total > 0 and i < len(variance) else 0.0 for i in range(3)]
    return coords, explained
