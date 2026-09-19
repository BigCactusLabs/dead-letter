"""Experimental analysis. Importing this package sends nothing and loads no SDK.

Preparation is local; remote execution requires explicit opt-in and environment BYOK.
"""

from dead_letter.analysis.contracts import AnalysisError, PreparedRequest, prepare_request
from dead_letter.analysis.eml import PreparedEmail, message_from_snapshot, prepare_eml
from dead_letter.analysis.profiles import get_profile
from dead_letter.analysis.responses import validate_response
from dead_letter.analysis.state import NormalizedMessage, Segment, build_state
from dead_letter.analysis.service import analyze_eml, analyze_prepared

__all__ = [
    "AnalysisError", "NormalizedMessage", "PreparedEmail", "PreparedRequest", "Segment",
    "analyze_eml", "analyze_prepared", "build_state", "get_profile", "message_from_snapshot",
    "prepare_eml", "prepare_request", "validate_response",
]
