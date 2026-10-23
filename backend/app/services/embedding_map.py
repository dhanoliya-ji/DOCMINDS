"""
embedding_map.py
================
WHAT THIS FILE DOES
-------------------
Squashes a project's chunk embeddings (hundreds of dimensions each) down to
three, so the search page can draw them as a point cloud
(GET /projects/{id}/embedding-map, frontend/components/EmbeddingCloud.tsx).

HOW: PCA, BY HAND, IN NUMPY
---------------------------
Principal Component Analysis finds the three directions along which the
vectors differ the most and reads each vector's position along them:

    1. Centre the matrix: subtract the mean vector, so the cloud sits on
       the origin.
    2. Singular value decomposition: X = U · S · Vᵀ. The rows of Vᵀ are the
       principal directions, strongest first.
    3. Project: X · V[:, :3] -- three coordinates per chunk.
    4. Scale into [-1, 1] by the largest absolute coordinate, so the
       frontend can use a fixed camera whatever the embedding model.

Chunks about the same thing have similar embeddings, so they land near each
other -- a handbook's leave policy forms one clump, its expenses rules
another. It is a picture, not a measurement: three dimensions keep only part
of the variance (`explained_variance` says how much).

No scikit-learn: numpy's SVD is all PCA needs, and it is already installed.
"""

from typing import Any, List, Sequence, Tuple

import numpy as np

# Cap on how many chunks are projected per request. The SVD of a
# 2000 x 1536 matrix takes well under a second; the browser draws 2000
# instanced points without effort. A larger project gets a sample.
MAX_POINTS = 2000


def as_vector(value: Any) -> np.ndarray:
    """
    One stored embedding as a float array.

    pgvector hands back a numpy array; SQLite (the test harness) the text form
    "[0.1, 0.2]"; a plain list is accepted too.
    """
    if isinstance(value, str):
        value = [float(part) for part in value.strip("[] ").split(",") if part.strip()]
    return np.asarray(value, dtype=np.float64)


def project_3d(vectors: Sequence[Any]) -> Tuple[np.ndarray, List[float]]:
    """
    PCA to three dimensions.

    Returns (coords, explained): `coords` is an (n, 3) array in [-1, 1];
    `explained` is the share of variance each axis keeps. Fewer than three
    usable directions (one or two chunks, or identical ones) leave the
    missing axes at zero rather than failing.
    """
    n = len(vectors)
    if n == 0:
        return np.zeros((0, 3)), [0.0, 0.0, 0.0]

    matrix = np.vstack([as_vector(v) for v in vectors])
    centred = matrix - matrix.mean(axis=0)

    # full_matrices=False: the "thin" SVD, all PCA needs, and far cheaper.
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
