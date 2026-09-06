from __future__ import annotations

import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# utf-8-sig evita BOM de PowerShell que rompe nombres de variables
load_dotenv(ROOT / ".env", encoding="utf-8-sig")


def _resolve_data_dir() -> Path:
    """En Vercel/Lambda el FS del proyecto es de solo lectura → usar /tmp."""
    if os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        path = Path(tempfile.gettempdir()) / "autotrade-cryptos"
        path.mkdir(parents=True, exist_ok=True)
        return path
    path = ROOT / "data"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_ok"
        probe.write_text("1", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return path
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "autotrade-cryptos"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


DATA_DIR = _resolve_data_dir()

CHAIN_ID = 480
CHAIN_NAME = "worldchain"
USDC_ADDRESS = "0x79A02482A880bCE3F13e09Da970dC34db4CD24d1"
USDC_DECIMALS = 6

ZERO_X_API_KEY = os.getenv("ZERO_X_API_KEY", "").strip()
WALLET = os.getenv("WALLET", "").strip()
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip().removeprefix("0x")
WORLDCHAIN_RPC_URL = os.getenv(
    "WORLDCHAIN_RPC_URL",
    "https://worldchain-mainnet.g.alchemy.com/public",
).strip()
# Lista separada por comas para ampliar el pool de fallback
# WORLDCHAIN_RPC_URLS=https://a...,https://b...
WORLDCHAIN_RPC_URLS = os.getenv("WORLDCHAIN_RPC_URLS", "").strip()

ZERO_X_BASE = "https://api.0x.org"
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/tokens/v1/{chain}/{address}"
CYCLE_SECONDS = 10 * 60
STATE_FILE = DATA_DIR / "bot_state.json"
OPERATIONS_FILE = DATA_DIR / "operations.json"

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    },
]