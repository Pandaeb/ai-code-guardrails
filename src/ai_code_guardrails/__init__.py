"""Deterministic guardrails for AI-written code.

Stdlib-only checks that run on a git diff (base...head) in CI or locally:
diff budget, scope discipline, test integrity, dependency policy, and
guard self-modification. Waivers are structurally human-only (PR labels).
"""

__version__ = "0.1.0"
