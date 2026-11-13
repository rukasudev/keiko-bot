"""Keiko's internal tooling.

Lives outside `app/` on purpose: this never ships in the deploy image (see
`.dockerignore`) and the dependency runs one way only — `scripts` may import
`app`, `app` may never import `scripts`. `tests/scripts/test_scripts_boundary.py`
keeps that honest.
"""
