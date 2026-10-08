"""
Crypto asset category service — reads manually-maintained sector tags from DB.

Categories are maintained manually via the UI.
No external API calls (CoinGecko was removed — too slow, rate-limited, incomplete).
"""
import json

from sqlalchemy.orm import Session

from backend.db.models import SymbolCategory
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def get_categories_for_symbols(
  symbols: list[str], db: Session
) -> dict[str, list[str]]:
  """Return {symbol: [category, ...]} from DB for given symbols."""
  rows = db.query(SymbolCategory).filter(
    SymbolCategory.symbol.in_(symbols)
  ).all()
  result: dict[str, list[str]] = {}
  for r in rows:
    try:
      result[r.symbol] = json.loads(r.categories)
    except (json.JSONDecodeError, TypeError):
      result[r.symbol] = []
  return result


def get_all_categories(db: Session) -> dict[str, list[str]]:
  """Return all categories {symbol: [labels]}."""
  rows = db.query(SymbolCategory).all()
  result: dict[str, list[str]] = {}
  for r in rows:
    try:
      result[r.symbol] = json.loads(r.categories)
    except (json.JSONDecodeError, TypeError):
      result[r.symbol] = []
  return result
