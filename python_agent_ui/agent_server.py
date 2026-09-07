import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from eth_account import Account
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from web3 import Web3

load_dotenv()

RPC_URL = os.getenv("WEB3_RPC_URL", "https://rpc.mtv.ac")
CHAIN_ID = int(os.getenv("CHAIN_ID", "62621"))
AGENT_PRIVATE_KEY = os.getenv("AGENT_PRIVATE_KEY", "").strip()

# Every transfer is a plain native send, so the gas limit is fixed. The
# per-run gas price is read once from the node and reused for every tx, so
# total fees are exactly gas_price * GAS_PER_TX * (number of transactions).
GAS_PER_TX = 21000
NATIVE_SYMBOL = os.getenv("NATIVE_SYMBOL", "MTV")

# Progress for the browser UI is written here after every batch so the
# "Transactions Submitted" panel can update live. app.py serves this same
# file from GET /api/agent/last-run.
PROGRESS_FILE = os.getenv(
    "AGENT_PROGRESS_FILE",
    os.path.join(os.path.dirname(__file__), "last_run.json"),
)


def _write_progress(data: dict[str, Any]) -> None:
    data = {**data, "source": "agent-server", "updatedAt": datetime.now(timezone.utc).isoformat()}
    tmp = PROGRESS_FILE + ".tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, PROGRESS_FILE)
    except OSError:
        pass

web3 = Web3(Web3.HTTPProvider(RPC_URL))

app = FastAPI(title="Local Agent Server")


class AgentRequest(BaseModel):
    prompt: str
    walletAddress: str
    chainId: int
    nonce: str
    signature: str
    batchCount: int = 1
    batchSize: int = 100
    dryRun: bool = False


def parse_native_transfer(prompt: str) -> tuple[float, str] | None:
    match = re.search(r"send\s+([0-9]+(?:\.[0-9]+)?)\s+native\s+MTV\s+to\s+(0x[a-fA-F0-9]{40})", prompt, flags=re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1)), match.group(2).lower()


def _is_retryable_rpc_error(message: str | None) -> bool:
    if not message:
        return False
    normalized = message.lower()
    retryable_markers = (
        "already known",
        "nonce too low",
        "replacement transaction underpriced",
        "transaction underpriced",
        "known transaction",
        "already imported",
    )
    return any(marker in normalized for marker in retryable_markers)


def build_native_transfer_tx(to_address: str, amount_eth: float) -> dict[str, Any]:
    if not AGENT_PRIVATE_KEY:
        raise ValueError("AGENT_PRIVATE_KEY is not configured for real transfers.")

    if not web3.is_connected():
        raise ValueError(f"RPC is not connected: {RPC_URL}")

    account = Account.from_key(AGENT_PRIVATE_KEY)
    tx_value_wei = Web3.to_wei(amount_eth, "ether")
    nonce = web3.eth.get_transaction_count(account.address)
    tx = {
        "chainId": CHAIN_ID,
        "nonce": nonce,
        "to": Web3.to_checksum_address(to_address),
        "value": tx_value_wei,
        "gas": GAS_PER_TX,
        "gasPrice": web3.eth.gas_price,
    }
    return tx


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "agent": "local-demo-agent",
        "message": "Agent server is running.",
        "rpcConnected": web3.is_connected(),
        "chainId": CHAIN_ID,
    }


MAX_TOTAL_TX = int(os.getenv("AGENT_MAX_TOTAL_TX", "20000"))


def _accepted_tokens() -> set[str]:
    tokens = {"local-dev-key"}
    for name in ("AGENT_TRIGGER_TOKEN", "AGENT_API_KEY"):
        value = os.getenv(name, "").strip()
        if value:
            tokens.add(value)
    return tokens


BROADCAST_ATTEMPTS = int(os.getenv("AGENT_BROADCAST_ATTEMPTS", "4"))
BROADCAST_WORKERS = int(os.getenv("AGENT_BROADCAST_WORKERS", "16"))

_TRANSPORT_MARKERS = (
    "ssl", "eof", "connection", "timed out", "timeout", "max retries",
    "reset by peer", "remote end closed", "temporarily unavailable",
    "bad gateway", "502", "503", "504",
)


def _is_transport_error(message: str | None) -> bool:
    if not message:
        return False
    normalized = message.lower()
    return any(marker in normalized for marker in _TRANSPORT_MARKERS)


def _broadcast_one(raw_tx) -> tuple[str, str | None]:
    """Send one pre-signed tx, retrying transient transport failures.

    Returns (status, value) where status is one of:
      "sent"    -> value is the tx hash
      "skipped" -> value is the node message (already known / nonce too low ...)
      "failed"  -> value is the last error message
    """
    last_err: str | None = None
    for attempt in range(BROADCAST_ATTEMPTS):
        try:
            tx_hash_hex = web3.eth.send_raw_transaction(raw_tx)
            return "sent", Web3.to_hex(tx_hash_hex)
        except Exception as send_exc:  # pragma: no cover
            message = str(send_exc)
            last_err = message
            if _is_retryable_rpc_error(message):
                return "skipped", message
            if _is_transport_error(message) and attempt < BROADCAST_ATTEMPTS - 1:
                time.sleep(0.25 * (attempt + 1))
                continue
            return "failed", message
    return "failed", last_err


@app.post("/trigger")
async def trigger(payload: AgentRequest, authorization: str | None = Header(default=None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
    else:
        token = None

    if token not in _accepted_tokens():
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")

    parsed = parse_native_transfer(payload.prompt)
    if parsed is None:
        return {
            "status": "accepted",
            "agent": "local-demo-agent",
            "walletAddress": payload.walletAddress,
            "chainId": payload.chainId,
            "nonce": payload.nonce,
            "signature": payload.signature,
            "prompt": payload.prompt,
            "txHash": "0xlocal-demo-agent-tx",
            "message": "Prompt was accepted but no native-transfer pattern was detected; local demo agent is in pass-through mode.",
        }

    amount_eth, recipient = parsed
    total_tx = max(1, min(int(payload.batchCount or 1), MAX_TOTAL_TX))
    batch_size = max(1, min(int(payload.batchSize or 100), total_tx))
    num_batches = (total_tx + batch_size - 1) // batch_size

    try:
        if not AGENT_PRIVATE_KEY:
            raise ValueError("AGENT_PRIVATE_KEY is not configured for real transfers.")
        if not web3.is_connected():
            raise ValueError(f"RPC is not connected: {RPC_URL}")

        sender = Account.from_key(AGENT_PRIVATE_KEY)
        recipient_cs = Web3.to_checksum_address(recipient)
        value_wei = Web3.to_wei(amount_eth, "ether")
        gas_price = web3.eth.gas_price
        base_nonce = web3.eth.get_transaction_count(sender.address)

        # Pre-sign every tx up front with contiguous nonces so the timed
        # section is pure broadcast throughput and batches never collide.
        raw_txs = []
        for i in range(total_tx):
            tx = {
                "chainId": CHAIN_ID,
                "nonce": base_nonce + i,
                "to": recipient_cs,
                "value": value_wei,
                "gas": GAS_PER_TX,
                "gasPrice": gas_price,
            }
            raw_txs.append(web3.eth.account.sign_transaction(tx, AGENT_PRIVATE_KEY).raw_transaction)

        tx_hashes: list[str] = []
        skipped = 0
        failed = 0
        first_error: str | None = None
        batches: list[dict[str, Any]] = []
        start_time = time.perf_counter()

        fee_per_tx_wei = GAS_PER_TX * gas_price

        def progress(state: str, elapsed: float) -> None:
            fee_tx_count = len(tx_hashes) + skipped
            total_fee_wei = fee_tx_count * fee_per_tx_wei
            _write_progress({
                "status": state,
                "dryRun": payload.dryRun,
                "target": total_tx,
                "submitted": len(tx_hashes),
                "skipped": skipped,
                "failed": failed,
                "firstError": first_error,
                "batchCount": num_batches,
                "batchSize": batch_size,
                "batchesDone": len(batches),
                "batches": batches,
                "startNonce": base_nonce,
                "recipient": recipient,
                "amountMtv": amount_eth,
                "totalTimeSeconds": round(elapsed, 3),
                "nativeSymbol": NATIVE_SYMBOL,
                "gasPerTx": GAS_PER_TX,
                "gasPriceWei": str(gas_price),
                "feePerTxWei": str(fee_per_tx_wei),
                "feePerTxMtv": float(Web3.from_wei(fee_per_tx_wei, "ether")),
                "feeTxCount": fee_tx_count,
                "totalFeeWei": str(total_fee_wei),
                "totalFeeMtv": float(Web3.from_wei(total_fee_wei, "ether")),
            })

        progress("running", 0.0)
        for b in range(num_batches):
            chunk = raw_txs[b * batch_size:(b + 1) * batch_size]
            batch_start = time.perf_counter()
            batch_hashes: list[str] = []
            batch_skipped = 0
            batch_failed = 0
            if payload.dryRun:
                batch_hashes = [f"0xdryrun-nonce-{base_nonce + b * batch_size + j}" for j in range(len(chunk))]
            else:
                workers = max(1, min(len(chunk), BROADCAST_WORKERS))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    for status, value in pool.map(_broadcast_one, chunk):
                        if status == "sent":
                            batch_hashes.append(value)
                        elif status == "skipped":
                            batch_skipped += 1
                        else:
                            batch_failed += 1
                            if first_error is None:
                                first_error = value
            batch_elapsed = time.perf_counter() - batch_start
            tx_hashes.extend(batch_hashes)
            skipped += batch_skipped
            failed += batch_failed
            batch_fee_tx_count = len(batch_hashes) + batch_skipped
            batch_fee_wei = batch_fee_tx_count * fee_per_tx_wei
            batches.append({
                "batch": b + 1,
                "requested": len(chunk),
                "sent": len(batch_hashes),
                "skipped": batch_skipped,
                "failed": batch_failed,
                "timeSeconds": round(batch_elapsed, 3),
                "firstHash": batch_hashes[0] if batch_hashes else None,
                "feeTxCount": batch_fee_tx_count,
                "feeWei": str(batch_fee_wei),
                "feeMtv": float(Web3.from_wei(batch_fee_wei, "ether")),
            })
            progress("running", time.perf_counter() - start_time)

        elapsed_seconds = time.perf_counter() - start_time
        accepted_total = len(tx_hashes) + skipped
        progress("done", elapsed_seconds)

        total_fee_wei = accepted_total * fee_per_tx_wei
        fee_fields = {
            "nativeSymbol": NATIVE_SYMBOL,
            "gasPerTx": GAS_PER_TX,
            "gasPriceWei": str(gas_price),
            "feePerTxWei": str(fee_per_tx_wei),
            "feePerTxMtv": float(Web3.from_wei(fee_per_tx_wei, "ether")),
            "feeTxCount": accepted_total,
            "totalFeeWei": str(total_fee_wei),
            "totalFeeMtv": float(Web3.from_wei(total_fee_wei, "ether")),
        }

        if not tx_hashes:
            return {
                "status": "accepted",
                "agent": "local-demo-agent",
                "walletAddress": payload.walletAddress,
                "chainId": payload.chainId,
                "nonce": payload.nonce,
                "signature": payload.signature,
                "prompt": payload.prompt,
                "txHash": None,
                "txHashes": [],
                "transactionCount": 0,
                "requestedCount": total_tx,
                "skippedCount": skipped,
                "failedCount": failed,
                "firstError": first_error,
                "batchCount": num_batches,
                "batchSize": batch_size,
                "batches": batches,
                "totalTimeSeconds": round(elapsed_seconds, 3),
                "totalTimeMs": int(round(elapsed_seconds * 1000)),
                "sender": sender.address,
                "recipient": recipient,
                "amountEth": amount_eth,
                **fee_fields,
                "message": (
                    f"No transactions were broadcast. {skipped} reported as already known, "
                    f"{failed} failed."
                    + (f" First error: {first_error}" if first_error else "")
                ),
            }

        return {
            "status": "accepted",
            "agent": "local-demo-agent",
            "walletAddress": payload.walletAddress,
            "chainId": payload.chainId,
            "nonce": payload.nonce,
            "signature": payload.signature,
            "prompt": payload.prompt,
            "txHash": tx_hashes[0],
            "txHashes": tx_hashes,
            "transactionCount": len(tx_hashes),
            "requestedCount": total_tx,
            "acceptedCount": accepted_total,
            "skippedCount": skipped,
            "failedCount": failed,
            "firstError": first_error,
            "batchCount": num_batches,
            "batchSize": batch_size,
            "batches": batches,
            "startNonce": base_nonce,
            "totalTimeSeconds": round(elapsed_seconds, 3),
            "totalTimeMs": int(round(elapsed_seconds * 1000)),
            "sender": sender.address,
            "recipient": recipient,
            "amountEth": amount_eth,
            "dryRun": payload.dryRun,
            **fee_fields,
            "message": (
                (f"DRY RUN: signed but did not broadcast {len(tx_hashes)} transactions "
                 if payload.dryRun else
                 f"Real native MTV transfer was broadcast successfully. {len(tx_hashes)} transactions sent ")
                + f"in {elapsed_seconds:.3f}s across {num_batches} batches of {batch_size}."
                + (f" ({skipped} already known, {failed} failed.)" if (skipped or failed) else "")
            ),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=9000)
