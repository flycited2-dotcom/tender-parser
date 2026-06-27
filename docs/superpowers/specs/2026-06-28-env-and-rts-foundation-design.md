# Env And RTS Foundation Design

## Goal

Make token-based tender sources safe to configure locally, then prepare RTS-Tender as a dedicated high-priority integration track.

## Scope

This increment has two deliverables:

1. Local secret handling for EAT and future cabinet/API sources.
2. RTS-Tender research and implementation strategy document.

The increment does not log into any cabinet, store real credentials, bypass captcha, or automate bid submission.

## Env Behavior

The parser should load a local `.env` file from the selected `--base-dir` before building default sources. This lets the user put token values in one local file instead of setting PowerShell variables every run.

Required behavior:

- `.env` is ignored by git.
- `.env.example` is committed with placeholder keys only.
- Existing OS environment variables win over `.env` values.
- Supported syntax is intentionally small: `KEY=value`, optional quotes, blank lines, and `#` comments.
- The loader should not print secret values.

Add a `check-env` CLI command that reads `.env`, reports whether EAT configuration is present, and exits:

- `0` when `EAT_API_TOKEN` and `EAT_EXT_SYSTEM` are configured;
- `1` when either required value is missing.

## RTS-Tender Foundation

RTS-Tender should be treated as a separate integration program because it is strategically important and the public pages can return captcha/rate-limit responses.

The RTS foundation document should cover:

- known public RTS market endpoints already in `RTS_MARKET_ENDPOINTS`;
- observed constraints: captcha, rate limits, SSL/network instability, and duplicates with EIS/Rostender;
- what to check in the RTS personal cabinet: API keys, XML/Excel exports, search subscriptions, saved filters, and notification feeds;
- target source split for future work:
  - public market source;
  - 223/44 public or official channel where available;
  - cabinet/API source if credentials are available;
  - EIS/Rostender dedup fallback for overlapping procedures.

## Success Criteria

- `python -m tender_parser check-env` works without network access.
- `python -m tender_parser run` still behaves as before, except it can now read EAT settings from `.env`.
- Test suite passes.
- Documentation tells the user exactly where to put EAT token values and what RTS information to look for in the cabinet.

