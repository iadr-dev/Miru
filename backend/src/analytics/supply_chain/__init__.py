"""Private supply-chain knowledge: relations between listed companies, each backed by evidence.

The operator's YAML files (under the private reference directory) are the source of truth; the
`sc_relations`/`sc_evidence` tables are an index rebuilt from them by `jobs.job_sc_refdata_load`.
This repository holds only code and synthetic test fixtures, never the data.
"""
