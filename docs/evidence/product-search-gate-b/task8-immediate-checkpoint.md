# Gate B Task 8 immediate owner checkpoint

The owner authorized one immediate initial Gate B launch at `2026-08-21T10:08:17Z`. This checkpoint replaces, and does not reuse, the previous prospective receipt.

- candidate commit: `56fdd5ae3192ba4efa962d7ac38719967281e732`
- package manifest: `6c3cbd6318e8e03ec58118103fd64ec7829fe5ed763837174546a08729f4953e`
- runtime manifest: `c9d91571c67977908d5e492de15529a27a5ee6a99c61e07d75ded8b66cf94517`
- runtime tree: `d0d4968d70b403e947326c9261fd76cd12a4cc42ac7789303751479e7fd8c4b4`
- Python executable: `cd6a26a9b2367f36eda6fa4381373d96c96f155b0ef8fae505f9f5e923b1c162`
- stdlib tree: `5d5643de0414ec4446d488aa5c606485f258bd29ed2b9f20c859be3843b88d4a`
- dependency lock: `e262172c0285bdfa9b2be095e3a2481dd620593dd440339170fa484cda8602cd`
- 69 installed distributions: `8fddf51dcf13d58533601b6ee0578bce2b885c236b4a10eda4f0e7ebb9fc8267`
- fixed sys.path: `8e63ea541c14f997b9ca4cb6dc417eef216a4003a17b2ad2214f0ad2f274cba8`
- launch identity: `6ea9044a5c7e1fa4bcf335afea7981e3d90566e0e01c088f98d011f0f0450886`
- owner checkpoint: `e67a82031f6f4dca1fe3be725f879e377673fb98c37dd5e09eb328afd41396c2`
- launch-ready bundle: `52f2bf860286376bbe791f6cc5ea6e30afddf6e1ea50d258a5969155979e86aa`
- receipt content: `c9af8cf00642c36ff2b4e9f7b4fce89f3277c218264024ccd401d0ce81012cb3`
- launch attempt: `gate-b-at-most-once-6c3cbd6318e8e03e-9257c8c0b356adb9abcd707499f30114828116edf4f42ba7316de72180a36a32`
- window: `2026-08-21T11:37:00Z` through `2026-08-21T12:07:00Z`
- owner recovery public-key SHA-256: `9435a761328f4c23783099a3edef822d0a9a870337e995bc3598a5dbf105846f`
- exact limit: 48 calls, USD 0.01 per call, USD 0.48 total
- verification: 641 passed; Ruff, unit, shell, scope, JSON, credential and provider-env checks passed

The two blocked preflights made zero provider calls and spent USD 0.00. The
first failed before package creation because the protected snapshot routed the
1,714,511,872-byte mutable `state.db` through the immutable-source 16 MB guard.
The second safely stopped before receipt installation because runtime identity
verification applied that same 16 MB default to the legitimate 30,845,896-byte
CPython 3.12.13 executable. The executable SHA matched the runtime manifest.

Commits `60be781c95756bb2a0f65f816b9da91bf1121b33`,
`e2fe977ace144aaa668ffd2c24013671091052a4`, and
`56fdd5ae3192ba4efa962d7ac38719967281e732` implement the reviewed narrow
policies. Mutable DB state and credentials remain metadata-only. Immutable
config/source stays content-hashed with the 16 MB cap. Only the trusted fixed
runtime executable has a 64 MB cap, while the existing nofollow, regular-file,
single-link, read-length, device, inode, size, mtime, and full-SHA checks remain.
Independent follow-up review found no Critical, Important, or Minor findings.

This authorizes only the one initial Task 9 execution. It does not authorize a retry, recovery launch, manual redispatch, Task 13, Gate C, Slack delivery, or production persistence.
