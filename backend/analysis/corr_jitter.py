"""Correlation matrix perturbation for candidate diversity.

Adds controlled random noise to correlation matrices so each portfolio
candidate explores slightly different asset relationships.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _nearest_pd(mat: np.ndarray) -> np.ndarray:
  """Find nearest positive semi-definite matrix via eigenvalue clamping."""
  eigvals, eigvecs = np.linalg.eigh(mat)
  eigvals = np.maximum(eigvals, 0.0)
  return eigvecs @ np.diag(eigvals) @ eigvecs.T


def perturb_correlation_matrix(
  corr: pd.DataFrame,
  temperature: float,
  seed: int | None = None,
) -> pd.DataFrame:
  """Add controlled random noise to a correlation matrix.

  - Adds uniform noise in [-temperature, +temperature] to off-diagonal
  - Clamps values to [-1, 1]
  - Forces diagonal = 1.0
  - Forces symmetry: (M + M.T) / 2
  - Ensures positive semi-definiteness via eigenvalue clamping
  """
  if temperature <= 0:
    return corr.copy()

  rng = np.random.default_rng(seed)
  vals = corr.values.copy().astype(float)
  n = vals.shape[0]

  # Add uniform noise to upper triangle, then mirror
  noise = rng.uniform(-temperature, temperature, size=(n, n))
  # Zero out diagonal noise
  np.fill_diagonal(noise, 0.0)
  # Make symmetric
  noise = (noise + noise.T) / 2.0

  perturbed = vals + noise
  # Clamp to [-1, 1]
  np.clip(perturbed, -1.0, 1.0, out=perturbed)
  # Force diagonal = 1.0
  np.fill_diagonal(perturbed, 1.0)
  # Force symmetry (handles FP drift)
  perturbed = (perturbed + perturbed.T) / 2.0

  # Ensure PSD
  eigvals = np.linalg.eigvalsh(perturbed)
  if eigvals.min() < -1e-10:
    perturbed = _nearest_pd(perturbed)
    np.fill_diagonal(perturbed, 1.0)
    np.clip(perturbed, -1.0, 1.0, out=perturbed)

  return pd.DataFrame(perturbed, index=corr.index, columns=corr.columns)
