# Mistakes Log

After any correction from the human, log the pattern here. Review at
session start before starting new work. The goal is to drive the error
rate toward zero.

Format:
- **Date** — *mistake* → **rule**

---

- **2026-05-27** — Trusted Bitget Republic mirror-credit disclosure
  ("1 token = 1 unit of post-IPO stock performance") at face value
  and set `share_ratio=1.0` for `OPENAI:bitget` (and earlier for
  `SPACEX:bitget`). The 1:1 holds **only at/after IPO conversion**;
  the live preXXX price is denominated against an undisclosed share
  basis that must be back-solved from the published subscription
  anchor. → **Rule**: For any Bitget IPO Prime preXXX token (or any
  Republic mirror-credit instrument), `ratio_source` must be
  `back-solved`, derived as `fully_diluted_shares / (announced_FDV /
  subscription_price)`. Never accept `ratio=1.0, source=official` for
  these — that's the misread to fix.

<!--
Examples:

- **2026-04-15** — Assumed `USER_ID` env var was set; silently fell back
  to empty string. → **Rule**: No silent fallbacks on env vars. Hard-fail
  at startup if required vars are missing.

- **2026-04-18** — Renamed `getUserPrefs` via grep; missed a dynamic
  import in `src/lib/legacy.ts`. → **Rule**: On every rename, search
  separately for static calls, type references, string literals, dynamic
  imports, re-exports, test mocks.
-->
