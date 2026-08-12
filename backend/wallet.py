from __future__ import annotations

from typing import Any, Dict, Optional

from eth_account import Account
from web3 import Web3

from .config import (
    ERC20_ABI,
    PRIVATE_KEY,
    USDC_ADDRESS,
    USDC_DECIMALS,
    WALLET,
)
from .logger import logs
from .rpc_pool import rpc_pool


class WalletError(RuntimeError):
    pass


def get_web3() -> Web3:
    try:
        return rpc_pool.get_web3()
    except Exception as exc:
        raise WalletError(f"No se pudo conectar a ningún RPC de World Chain: {exc}") from exc


def require_credentials() -> tuple[str, str]:
    if not WALLET or not PRIVATE_KEY:
        raise WalletError("Configura WALLET y PRIVATE_KEY en el archivo .env")
    acct = Account.from_key(PRIVATE_KEY)
    if acct.address.lower() != WALLET.lower():
        raise WalletError("WALLET no coincide con la PRIVATE_KEY")
    return Web3.to_checksum_address(WALLET), PRIVATE_KEY


def erc20(w3: Web3, address: str):
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)


def get_balance(token: str, owner: Optional[str] = None) -> int:
    w3 = get_web3()
    owner_addr = Web3.to_checksum_address(owner or WALLET)
    return int(erc20(w3, token).functions.balanceOf(owner_addr).call())


def get_usdc_balance_human() -> float:
    raw = get_balance(USDC_ADDRESS)
    return raw / (10**USDC_DECIMALS)


def get_token_decimals(token: str) -> int:
    w3 = get_web3()
    try:
        return int(erc20(w3, token).functions.decimals().call())
    except Exception:
        return 18


def wallet_usdc_snapshot() -> Dict[str, Any]:
    """Balance USDC de la wallet del .env. Sin WALLET → 0.00."""
    if not WALLET:
        return {
            "configured": False,
            "wallet": None,
            "usdc_balance": 0.0,
            "error": None,
        }
    short = f"{WALLET[:6]}…{WALLET[-4:]}" if len(WALLET) > 10 else WALLET
    try:
        bal = get_usdc_balance_human()
        return {
            "configured": True,
            "wallet": short,
            "wallet_full": WALLET,
            "usdc_balance": bal,
            "error": None,
        }
    except Exception as exc:
        return {
            "configured": True,
            "wallet": short,
            "wallet_full": WALLET,
            "usdc_balance": 0.0,
            "error": str(exc),
        }


def ensure_allowance(
    *,
    token: str,
    spender: str,
    amount: int,
) -> Optional[str]:
    """Approve spender if current allowance is insufficient. Returns tx hash or None."""
    w3 = get_web3()
    wallet, key = require_credentials()
    contract = erc20(w3, token)
    spender_cs = Web3.to_checksum_address(spender)
    current = int(contract.functions.allowance(wallet, spender_cs).call())
    if current >= amount:
        return None

    # Reset to 0 first if needed (some tokens require it)
    nonce = w3.eth.get_transaction_count(wallet)
    if current > 0:
        tx0 = contract.functions.approve(spender_cs, 0).build_transaction(
            {
                "from": wallet,
                "nonce": nonce,
                "chainId": w3.eth.chain_id,
                "gas": 100_000,
            }
        )
        signed0 = w3.eth.account.sign_transaction(tx0, private_key=key)
        h0 = w3.eth.send_raw_transaction(signed0.raw_transaction)
        w3.eth.wait_for_transaction_receipt(h0, timeout=180)
        nonce += 1

    tx = contract.functions.approve(spender_cs, amount).build_transaction(
        {
            "from": wallet,
            "nonce": nonce,
            "chainId": w3.eth.chain_id,
            "gas": 120_000,
        }
    )
    signed = w3.eth.account.sign_transaction(tx, private_key=key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise WalletError("Approve falló on-chain")
    return tx_hash.hex()


def send_swap_tx(quote: Dict[str, Any]) -> str:
    w3 = get_web3()
    wallet, key = require_credentials()
    tx_data = quote.get("transaction") or {}
    if not tx_data.get("to") or not tx_data.get("data"):
        raise WalletError("Quote 0x sin transaction.to/data")

    # Handle allowance if indicated by 0x
    issues = quote.get("issues") or {}
    allowance = issues.get("allowance")
    if allowance and allowance.get("spender"):
        sell_token = (quote.get("sellToken") or "").strip()
        sell_amount = int(quote.get("sellAmount") or 0)
        if sell_token and sell_amount > 0:
            # Fire-and-forget style under sync API — caller logs around this
            ensure_allowance(
                token=sell_token,
                spender=allowance["spender"],
                amount=sell_amount,
            )

    # Also check allowanceTarget field (legacy / alternate)
    allowance_target = quote.get("allowanceTarget")
    if allowance_target and not (allowance and allowance.get("spender")):
        sell_token = (quote.get("sellToken") or "").strip()
        sell_amount = int(quote.get("sellAmount") or 0)
        if sell_token and sell_amount > 0:
            ensure_allowance(
                token=sell_token,
                spender=allowance_target,
                amount=sell_amount,
            )

    value = int(tx_data.get("value") or 0)
    gas = int(tx_data["gas"]) if tx_data.get("gas") else None
    gas_price = int(tx_data["gasPrice"]) if tx_data.get("gasPrice") else None

    tx: Dict[str, Any] = {
        "from": wallet,
        "to": Web3.to_checksum_address(tx_data["to"]),
        "data": tx_data["data"],
        "value": value,
        "nonce": w3.eth.get_transaction_count(wallet),
        "chainId": w3.eth.chain_id,
    }
    if gas is not None:
        tx["gas"] = gas
    else:
        tx["gas"] = w3.eth.estimate_gas(tx)
    if gas_price is not None:
        tx["gasPrice"] = gas_price

    signed = w3.eth.account.sign_transaction(tx, private_key=key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=240)
    if receipt.status != 1:
        raise WalletError(f"Swap falló on-chain: {tx_hash.hex()}")
    return tx_hash.hex()


async def approve_and_swap(quote: Dict[str, Any]) -> str:
    """Async wrapper that logs approval + swap steps."""
    issues = quote.get("issues") or {}
    allowance = issues.get("allowance")
    spender = None
    if allowance and allowance.get("spender"):
        spender = allowance["spender"]
    elif quote.get("allowanceTarget"):
        spender = quote["allowanceTarget"]

    if spender:
        sell_token = quote.get("sellToken")
        sell_amount = int(quote.get("sellAmount") or 0)
        await logs.emit(
            f"Revisando allowance → spender {spender[:10]}…",
            kind="swap",
            level="info",
        )
        tx_approve = ensure_allowance(
            token=sell_token,
            spender=spender,
            amount=sell_amount,
        )
        if tx_approve:
            await logs.emit(
                f"Approve OK: {tx_approve}",
                kind="swap",
                level="success",
                data={"tx": tx_approve},
            )
        else:
            await logs.emit("Allowance suficiente, skip approve", kind="swap")

    # Strip issues so send_swap_tx does not double-approve
    quote_copy = dict(quote)
    quote_copy["issues"] = {}
    quote_copy.pop("allowanceTarget", None)

    await logs.emit("Enviando swap on-chain…", kind="swap", level="info")
    tx_hash = send_swap_tx(quote_copy)
    await logs.emit(
        f"Swap confirmado: {tx_hash}",
        kind="swap",
        level="success",
        data={"tx": tx_hash},
    )
    return tx_hash