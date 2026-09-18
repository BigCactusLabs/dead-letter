"""Experimental offline analysis preparation. Importing this package sends nothing.

EML snapshots and CLI previews are available; remote transport remains disabled.
"""

from dead_letter.analysis.contracts import AnalysisError, PreparedRequest, prepare_request
from dead_letter.analysis.eml import PreparedEmail, message_from_snapshot, prepare_eml
from dead_letter.analysis.profiles import get_profile
from dead_letter.analysis.responses import validate_response
from dead_letter.analysis.state import NormalizedMessage, Segment, build_state

__all__ = [
    "AnalysisError", "NormalizedMessage", "PreparedEmail", "PreparedRequest", "Segment",
    "build_state", "get_profile", "message_from_snapshot", "prepare_eml",
    "prepare_request", "validate_response",
]
