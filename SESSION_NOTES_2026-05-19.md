# Session Notes — 2026-05-19

## SpaceX pre-IPO market metadata work

- Repeatedly corrected SpaceX venue-implied valuation assumptions after identifying scale/decimal misreads in synthetic market price interpretations.
- Kept Hyperliquid `SPCX` as `uncalibrated` because core-universe routing is not implemented for the HIP-3 builder-deployed market path.
- Updated OKX SpaceX market notes multiple times to reflect venue-implied logic (`share_ratio=1.0` assumption from contract metadata) and caveats that these are synthetic venue levels rather than fundamental valuation.
- Removed/avoided speculative Bitget implied FDV values when live ticker verification could not be reliably captured in this execution environment.
- Added Gate SpaceX market entry (`SPCX_USDT`) with source-backed documentation context and intentionally left uncalibrated until verifiable shares-per-token conversion and live ticker capture are available.

## Data integrity and process

- Maintained JSON validity checks after market metadata edits.
- Preserved a conservative policy: when data provenance is weak or unverifiable, keep calibration null and annotate limitations in notes.
