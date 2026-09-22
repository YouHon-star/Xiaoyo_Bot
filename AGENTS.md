# Agent Instructions (Xiaoyo-Bot)

This `AGENTS.md` is for agentic coding tools working in this repository. Keep it factual and repo-backed.

## Repo Map
- `pyproject.toml`: tool configs + NoneBot plugin discovery (`[tool.nonebot]`).
- `xiaoyo_bot/plugins/`: local plugins (loaded via `plugin_dirs`).
- `storage/`: runtime temp files.
- `xiaoyo_open.bat`: Windows launcher (starts NapCat, activates `.venv`, runs NoneBot).

## Commands: Build / Lint / Typecheck / Test
### Setup a virtual environment
```bash
python -m venv .venv
```

Windows:
```bat
.venv\Scripts\activate.bat
```

Linux/macOS:
```bash
source .venv/bin/activate
```

Install project (editable):
```bash
pip install -e .
```

### Run the bot (NoneBot CLI)
- Dev (hot reload): `nb run --reload` (documented in `README.md`).
- Normal run: `nb run` (used in `xiaoyo_open.bat`).
- Windows one-shot launcher: run `xiaoyo_open.bat`.
  - Note: it hardcodes a NapCat path: `C:\Users\Yohon\Files\Xiaoyo Bot`.

### Lint / Format (Ruff)
Ruff is configured in `pyproject.toml` under `[tool.ruff]`, `[tool.ruff.format]`, `[tool.ruff.lint]`.

```bash
ruff check .
ruff check . --fix
ruff format .
```

### Typecheck (Pyright)
Pyright is configured in `pyproject.toml` under `[tool.pyright]`.

```bash
pyright
```

### Tests
- No test suite is currently present in this repo (no `tests/`, no `pytest.ini`, no pytest dependency).
- “Run a single test”: N/A until a test runner is introduced.

## Cursor / Copilot Rules
- Cursor rules: none found (no `.cursor/rules/`, no `.cursorrules`).
- Copilot instructions: none found (no `.github/copilot-instructions.md`).

## Code Style (Repo-Backed)
### Formatting
- Max line length: 88 (`pyproject.toml:[tool.ruff].line-length`).
- Line endings: LF (`pyproject.toml:[tool.ruff.format].line-ending = "lf"`).
- Use `ruff format .` before finalizing changes.

### Imports
- Import sorting is enabled via Ruff isort (`I`).
- Existing code sometimes imports mid-file (e.g. `guess_song/__init__.py`).
- Ruff explicitly ignores these (to accommodate framework patterns):
  - `E402` (module-import-not-at-top-of-file)
  - `PLC0415` (import-outside-top-level)
- Still: prefer top-level imports unless a framework constraint requires otherwise.

### Typing
- Pyright mode: `standard`.
- Code uses Python 3.10+ union syntax (`int | None`, `str | None`) in plugins.
- New helper functions should be annotated; keep new code consistent within the file you touch.
- Prefer concrete OneBot v11 types in handler signatures when available:
  - `Bot`, `MessageEvent`, `GroupMessageEvent`, etc.

Note: `pyproject.toml` sets `pythonVersion = "3.9"` for Pyright, but the project requires Python `>=3.10`.
If that mismatch causes noise, fix `pyproject.toml` (don't paper over it in code).

### Naming
- Files/packages: snake_case (e.g. `get_song.py`, `ai_module/`).
- Constants: UPPER_SNAKE_CASE for configuration/tuning values.
- Handler functions:
  - Often named `handle_*`.
  - Short local handlers sometimes use `_`.

### Error handling
- Existing code frequently uses broad `except Exception` and returns a user-facing Chinese message.
- When adding new code:
  - Avoid bare `except:`.
  - Log unexpected failures via NoneBot `logger` when it helps debugging.
  - Keep user-visible errors short and actionable.

### Logging
- Use NoneBot `logger` (`from nonebot import logger`).
- Avoid printing to stdout (Ruff enables `T20`).

## NoneBot / Plugin Conventions
### Where plugins live
- Local plugin dir is `xiaoyo_bot/plugins` (`pyproject.toml:[tool.nonebot].plugin_dirs`).

### Matchers and priorities (observed)
- Commands: `on_command(...)` with Chinese trigger phrases.
- Message listeners: `on_message(...)` for ambient/stateful flows.
- Priorities used in this repo:
  - 2: owner/admin controls (`owner_control.py`)
  - 3: games (`guess_song/__init__.py`)
  - 4: general features + AI listener (`get_song.py`, `ai_module/__init__.py`)

Behavior patterns: `block=True` for commands, `block=False` for listeners; use `.send()` for progress and `.finish()` to end.

### State management
- Current plugins store state in module-level dicts/sets (in-memory).
- If you add stateful flows, include timeouts and cleanup to avoid unbounded growth.

## Configuration & Secrets
- `.env` is gitignored; repo also contains `.env.dev` and `.env.prod`.
- AI plugin reads environment variables:
  - `AI_API_KEY`
  - `AI_MODEL`

Do not commit credentials/tokens/cookies.
- `xiaoyo_bot/plugins/get_song.py` currently contains a large hardcoded cookie-like string;
  treat it as sensitive during refactors.

## Practical Workflow For Agents
1. Make small, scoped edits.
2. Run `ruff format .` and `ruff check .` (or at least on touched files).
3. Run `pyright` when touching types/handlers.
4. For runtime behavior changes, run the bot locally (`nb run --reload`) and
   exercise the command in a test group/chat.
