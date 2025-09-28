# Repository Guidelines

## Project Structure & Module Organization
- `Scripts/` hosts runnable Simba scripts; `Scripts\wasp-launcher.simba` is the launcher that links accounts, assets, and bundled scripts.
- Vendor folders such as `Scripts\waspscripts.com\` and `Scripts\gonkscripts.com\` contain production-ready examples to mirror for new automation.
- Shared libraries live in `Includes/WaspLib/` (primary) and `Includes/SRL-T/` (low-level SRL routines); prefer extending these includes over duplicating logic.
- Configuration lives under `Configs/` (`launcher.json`, `wasplib.json`, `stats.json`), while `Data/` stores cached downloads (`Data/wasp-launcher/`, `Data/WaspLib/`) and `Fonts/` provides required GUI assets.
- Native plugins reside in `Includes/*/plugins/`; confirm matching binaries when upgrading Simba (current bundle targets Simba 1400).

## Build, Test, and Development Commands
- `Simba64.exe`: launch the GUI, open `Scripts\wasp-launcher.simba`, and run interactively for day-to-day debugging.
- `Simba64.exe --script "Scripts\\wasp-launcher.simba"` runs the launcher headless; swap the path for individual scripts when smoke-testing a new module.
- Toggle auto-update flags (`update_srl_checkbox`, `update_wl_checkbox`) in `Configs/launcher.json` before first run on a new machine to pull the latest SRL-T/WaspLib bundles.

## Coding Style & Naming Conventions
- Follow PascalScript norms: two-space indentation, aligned `begin`/`end`, and trimmed trailing whitespace.
- Use PascalCase for types and routines (`TWaspClient`, `RunTests`), UPPER_SNAKE for constants, and camelCase for locals and fields.
- Document shared procedures with `(* ... *)` blocks, reserve `//` for short inline notes, and group compiler directives (`{$DEFINE ...}`) at the top of each unit.
- Reach for WaspLib utilities (`Includes/WaspLib/utils/`, `.../osr/`) before writing bespoke helpers.

## Testing Guidelines
- Keep `{$DEFINE DEVELOPER_MODE}` enabled while iterating; call `WaspClient.RunTests` inside the launcher to validate API endpoints, subscriptions, and entitlement flows.
- Refresh credentials in `Configs/launcher.json` (email, tokens) and sanitize `credentials.simba` before sharing logs.
- Exercise new scripts through the launcher so assets populate under `Data/`; capture console output for regressions instead of screenshots when possible.

## Commit & Pull Request Guidelines
- Write focused commits with short, imperative summaries (`clean up`, `add documentation files`) consistent with the existing history.
- In commit bodies or PR descriptions, list impacted scripts, config changes, required data resets, and note any Simba version dependency.
- PRs should link related waspscripts.com tasks, describe testing performed, and include console excerpts or progress metrics when behavior changes.

## Security & Configuration Tips
- Never push live credentials or refresh tokens - replace with placeholders and keep personal overrides in ignored files.
- Purge transient caches in `Data/` and remove debug screenshots before requesting review to avoid leaking account data.
