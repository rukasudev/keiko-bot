"""Keiko's internal tooling.

Lives outside `app/` on purpose: this never ships in the deploy image (see
`.dockerignore`) and the dependency runs one way only — `tools` may import
`app`, `app` may never import `tools`. `tests/tools/test_tools_boundary.py`
keeps that honest.
"""
