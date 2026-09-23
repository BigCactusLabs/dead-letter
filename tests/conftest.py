"""Bounded property tests in CI, with an opt-in longer local profile."""

from __future__ import annotations

import os

from hypothesis import HealthCheck, settings


settings.register_profile("ci", derandomize=True, max_examples=30, deadline=500, print_blob=True)
settings.register_profile(
    "fuzz", max_examples=300, deadline=None, print_blob=True,
    suppress_health_check=(HealthCheck.too_slow,),
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
