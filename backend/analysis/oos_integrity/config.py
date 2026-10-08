"""OOS Integrity Agent — audit configuration."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AuditConfig:
  """Configuration for OOS integrity audit.

  All thresholds have conservative defaults (paranoid mode).
  Toggle individual scanners on/off via enable_* flags.
  """
  # Thresholds
  similarity_threshold: float = 0.95
  psi_threshold: float = 0.25
  adversarial_auc_threshold: float = 0.55
  temporal_overlap_tolerance: int = 0
  performance_gap_alert: float = 0.02
  embargo_days: int = 0
  max_autocorr_lag: int = 30
  benford_chi2_threshold: float = 15.507  # chi2 critical value df=8 p=0.05
  price_jump_threshold: float = 0.50  # 50% max single-bar price change

  # Scanner toggles
  enable_temporal: bool = True
  enable_identity: bool = True
  enable_feature: bool = True
  enable_preprocessing: bool = True
  enable_distribution: bool = True
  enable_statistical: bool = True
  enable_pipeline: bool = True
  enable_group: bool = True
  enable_wfo_fold: bool = True
  enable_quant_finance: bool = True
  enable_truthfulness: bool = True
  enable_timestamp: bool = True
  enable_schema: bool = True

  # Output
  verbose: bool = True
