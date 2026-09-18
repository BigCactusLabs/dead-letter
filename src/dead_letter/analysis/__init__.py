"""Experimental, offline analysis contracts. Importing this package sends nothing.

The TypeSafe transport and EML/CLI integration are intentionally not connected yet.
"""

from dead_letter.analysis.contracts import AnalysisError, PreparedRequest, prepare_request
from dead_letter.analysis.profiles import get_profile
from dead_letter.analysis.responses import validate_response
from dead_letter.analysis.state import NormalizedMessage, Segment, build_state

__all__ = [
    "AnalysisError", "NormalizedMessage", "PreparedRequest", "Segment",
    "build_state", "get_profile", "prepare_request", "validate_response",
]
