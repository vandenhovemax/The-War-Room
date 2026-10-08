"""Validator registry — data truthfulness and schema checks."""
from backend.analysis.oos_integrity.validators.schema import SchemaValidator
from backend.analysis.oos_integrity.validators.timestamp import TimestampValidator
from backend.analysis.oos_integrity.validators.truthfulness import TruthfulnessValidator

ALL_VALIDATORS = [
  TimestampValidator,
  TruthfulnessValidator,
  SchemaValidator,
]
