"""The safety measurement of the suite: does it still cover and catch what it did?

make test-guard                                   # measure and compare to baseline.json
make test-guard ARGS="--json out.json"            # plus the full measurement as JSON
make test-guard ARGS="--save-baseline"            # record this tree as the baseline
python -m scripts.keiko testguard ledger [--sync] # the past-bugs ledger, in a second
python -m scripts.keiko testguard show <id>       # what one mutant changes
python -m scripts.keiko testguard mutants         # the ids of the mutant set

Every run works on throwaway copies of the working tree and measures five things:
line and branch coverage of app/, a fixed seeded set of mutants, the past bugs of
`ledger.yml`, the cost of five standard changes and the suite's time. A mutant
`accepted.yml` lists (its baseline kill was shown false, with the reason and the
command that shows it) is skipped by the kill check and reported as accepted.
"""
