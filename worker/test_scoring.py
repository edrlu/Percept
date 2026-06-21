"""Model-free tests for the fixed-reference scoring math (no GPU / no TRIBE model)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import reference_stats  # noqa: E402


def test_reference_stats_are_per_vertex_mean_and_sd():
    pred = np.array([[0.0, 10.0], [2.0, 10.0], [4.0, 10.0]])  # (T=3, V=2)
    mu, sd = reference_stats(pred)
    assert np.allclose(mu, [2.0, 10.0])
    assert np.allclose(sd[0], np.std([0.0, 2.0, 4.0]))
    assert sd[1] >= 1e-6  # zero-variance vertex is floored, never divides by 0


def test_self_reference_centers_at_zero_other_reference_shifts():
    take = np.array([[5.0], [7.0], [9.0]])           # mean 7
    own_mu, own_sd = reference_stats(take)
    z_self = ((take - own_mu) / own_sd).mean()
    assert abs(z_self) < 1e-9                          # vs itself -> centered (~50 after scaling)
    orig = np.array([[1.0], [3.0], [5.0]])             # original baseline, mean 3
    ref_mu, ref_sd = reference_stats(orig)
    z_vs_orig = ((take - ref_mu) / ref_sd).mean()
    assert z_vs_orig > 0.5                             # the take sits clearly above the original


def test_npz_roundtrip(tmp_path=Path("/tmp")):
    mu = np.arange(20484, dtype=np.float64)
    sd = np.ones(20484, dtype=np.float64)
    f = Path(tmp_path) / "_ref_roundtrip.npz"
    np.savez(f, mu=mu, sd=sd)
    data = np.load(f)
    assert np.array_equal(data["mu"], mu) and np.array_equal(data["sd"], sd)
    f.unlink()


if __name__ == "__main__":
    test_reference_stats_are_per_vertex_mean_and_sd()
    test_self_reference_centers_at_zero_other_reference_shifts()
    test_npz_roundtrip()
    print("OK: all scoring-math tests passed")
