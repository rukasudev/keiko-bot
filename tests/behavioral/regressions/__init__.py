# Permanent regression scenarios.
#
# Every test here protects behavior that ONCE BROKE in production or review.
# Rules (see docs/testing-strategy.md):
# - the test NAME describes the lasting contract, not the incident;
# - the DOCSTRING records what broke, which shared behavior was affected,
#   which consumer exposed it, and what must remain guaranteed;
# - tests here are never deleted when they pass — that is the point.
