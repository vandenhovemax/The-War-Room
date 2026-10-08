"""Scanner registry — imports all scanners for the orchestrator."""
from backend.analysis.oos_integrity.scanners.distribution import DistributionShiftScanner
from backend.analysis.oos_integrity.scanners.feature import FeatureLeakageScanner
from backend.analysis.oos_integrity.scanners.group import GroupLeakageScanner
from backend.analysis.oos_integrity.scanners.identity import IdentityLeakageScanner
from backend.analysis.oos_integrity.scanners.pipeline import PipelineTracerScanner
from backend.analysis.oos_integrity.scanners.preprocessing import PreprocessingLeakageScanner
from backend.analysis.oos_integrity.scanners.quant_finance import QuantFinanceScanner
from backend.analysis.oos_integrity.scanners.statistical import StatisticalValidationScanner
from backend.analysis.oos_integrity.scanners.temporal import TemporalLeakageScanner
from backend.analysis.oos_integrity.scanners.wfo_fold import WFOFoldScanner

ALL_SCANNERS = [
  TemporalLeakageScanner,
  IdentityLeakageScanner,
  FeatureLeakageScanner,
  PreprocessingLeakageScanner,
  DistributionShiftScanner,
  StatisticalValidationScanner,
  PipelineTracerScanner,
  GroupLeakageScanner,
  WFOFoldScanner,
  QuantFinanceScanner,
]
