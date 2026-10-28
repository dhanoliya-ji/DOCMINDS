import numpy as np
import pytest

from app.services import embedding_map


class TestProject3d:
    def test_shape_and_range(self):
        rng = np.random.default_rng(0)
        coords, explained = embedding_map.project_3d(rng.normal(size=(50, 16)))
        assert coords.shape == (50, 3)
        assert np.abs(coords).max() == pytest.approx(1.0)
        assert len(explained) == 3 and 0 < sum(explained) <= 1.0001
        assert explained[0] >= explained[1] >= explained[2]

    def test_neighbours_stay_close(self):
        rng = np.random.default_rng(1)
        a = rng.normal(0, 0.01, size=(10, 32)) + 5
        b = rng.normal(0, 0.01, size=(10, 32)) - 5
        coords, _ = embedding_map.project_3d(np.vstack([a, b]))
        within = np.linalg.norm(coords[0] - coords[1])
        across = np.linalg.norm(coords[0] - coords[10])
        assert within < across / 10

    def test_tiny_and_degenerate_inputs(self):
        assert embedding_map.project_3d([])[0].shape == (0, 3)
        coords, _ = embedding_map.project_3d([[1.0, 2.0]])
        assert coords.tolist() == [[0.0, 0.0, 0.0]]
        coords, _ = embedding_map.project_3d([[1, 2, 3], [1, 2, 3]])
        assert np.all(coords == 0)

    def test_text_form_vectors_are_parsed(self):
        assert embedding_map.as_vector("[0.5, -1, 2]").tolist() == [0.5, -1.0, 2.0]
