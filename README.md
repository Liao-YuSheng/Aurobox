# Aurobox-flashbot

Aurobox Flashbot controller package for PUDU robot API.

## Quick Start

1. Copy `.env.example` to `.env` and fill in your credentials.
2. Install dependencies:

```bash
python -m pip install -e .
```

3. Run the CLI:

```bash
python -m aurobox.cli --help
```

## Example

```bash
python -m aurobox.cli status --sn 8FF055923050007
```

## Project Layout

- `src/aurobox/config.py`: load environment and config
- `src/aurobox/pudu_client.py`: PUDU API client and HMAC signing
- `src/aurobox/robot.py`: Flashbot controller wrapper
- `src/aurobox/cli.py`: command-line interface
- `tests/test_pudu_client.py`: basic package tests
