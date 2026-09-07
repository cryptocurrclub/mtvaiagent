import asyncio
import json
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from eth_account import Account
from eth_account.messages import encode_typed_data
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from web3 import Web3

load_dotenv()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
RPC_URL = os.getenv("WEB3_RPC_URL", "https://mainnet.infura.io/v3/your-project-id")
CHAIN_ID = int(os.getenv("CHAIN_ID", "1"))
TARGET_AGENT_URL = os.getenv("TARGET_AGENT_URL", "")
AGENT_API_KEY = os.getenv("AGENT_API_KEY", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-secret")
APP_NAME = os.getenv("APP_NAME", "AI Agent Wallet UI")

# Native token + CoinGecko price lookup for fee conversion to USD.
NATIVE_SYMBOL = os.getenv("NATIVE_SYMBOL", "MTV")
COINGECKO_PRICE_URL = os.getenv(
    "COINGECKO_PRICE_URL", "https://api.coingecko.com/api/v3/simple/price"
)
COINGECKO_ID = os.getenv("COINGECKO_ID", "multivac")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "").strip()
PRICE_TTL_SECONDS = int(os.getenv("PRICE_TTL_SECONDS", "60"))
GAS_PER_TX_FALLBACK = 21000

# "Run Forever" background batch runner (asyncio task + SSE fan-out).
FOREVER_MIN_INTERVAL_SECONDS = float(os.getenv("FOREVER_MIN_INTERVAL_SECONDS", "3"))
FOREVER_MAX_INTERVAL_SECONDS = float(os.getenv("FOREVER_MAX_INTERVAL_SECONDS", "3600"))
FOREVER_DEFAULT_INTERVAL_SECONDS = float(os.getenv("FOREVER_DEFAULT_INTERVAL_SECONDS", "10"))
FOREVER_MAX_CYCLES = int(os.getenv("FOREVER_MAX_CYCLES", "0"))  # 0 = unlimited
FOREVER_MAX_CONSECUTIVE_FAILURES = int(os.getenv("FOREVER_MAX_CONSECUTIVE_FAILURES", "5"))
FOREVER_CIRCUIT_COOLDOWN_SECONDS = float(os.getenv("FOREVER_CIRCUIT_COOLDOWN_SECONDS", "60"))
FOREVER_BACKOFF_BASE_SECONDS = float(os.getenv("FOREVER_BACKOFF_BASE_SECONDS", "2"))
FOREVER_BACKOFF_CAP_SECONDS = float(os.getenv("FOREVER_BACKOFF_CAP_SECONDS", "60"))
FOREVER_CYCLE_TIMEOUT_SECONDS = float(os.getenv("FOREVER_CYCLE_TIMEOUT_SECONDS", "300"))
FOREVER_SSE_KEEPALIVE_SECONDS = float(os.getenv("FOREVER_SSE_KEEPALIVE_SECONDS", "15"))
FOREVER_SSE_MAX_QUEUE = int(os.getenv("FOREVER_SSE_MAX_QUEUE", "64"))
FOREVER_CYCLE_LOG_MAX = int(os.getenv("FOREVER_CYCLE_LOG_MAX", "200"))

app = FastAPI(title=APP_NAME)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

web3 = Web3(Web3.HTTPProvider(RPC_URL))
RPC_CONNECTED = False
try:
    RPC_CONNECTED = bool(RPC_URL) and web3.is_connected()
except Exception:  # pragma: no cover
    RPC_CONNECTED = False

base_templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


class WalletAddressRequest(BaseModel):
    address: str = Field(..., min_length=42, max_length=42)

    @field_validator("address")
    @classmethod
    def validate_wallet_address(cls, value: str) -> str:
        cleaned = value.strip()
        if not Web3.is_address(cleaned):
            raise ValueError("Invalid Ethereum address")
        return Web3.to_checksum_address(cleaned)


class ChallengeRequest(WalletAddressRequest):
    chainId: int = Field(default=CHAIN_ID, ge=1)


class AuthVerifyRequest(BaseModel):
    address: str
    nonce: str
    signature: str
    chainId: int = Field(default=CHAIN_ID, ge=1)

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        cleaned = value.strip()
        if not Web3.is_address(cleaned):
            raise ValueError("Invalid Ethereum address")
        return Web3.to_checksum_address(cleaned)


class TriggerRequest(BaseModel):
    address: str
    signature: str
    nonce: str
    prompt: str = Field(..., min_length=1, max_length=2000)
    chainId: int = Field(default=CHAIN_ID, ge=1)
    batchCount: int = Field(default=100, ge=1, le=20000)
    batchSize: int = Field(default=100, ge=1, le=20000)

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        cleaned = value.strip()
        if not Web3.is_address(cleaned):
            raise ValueError("Invalid Ethereum address")
        return Web3.to_checksum_address(cleaned)


class SessionStore:
    _nonces: dict[str, dict[str, Any]] = {}

    @classmethod
    def put(cls, address: str, nonce: str, issued_at: datetime) -> None:
        cls._nonces[address.lower()] = {"nonce": nonce, "issued_at": issued_at}

    @classmethod
    def get(cls, address: str) -> dict[str, Any] | None:
        return cls._nonces.get(address.lower())

    @classmethod
    def remove(cls, address: str) -> None:
        cls._nonces.pop(address.lower(), None)


JOB_STORE: dict[str, dict[str, Any]] = {}


async def run_agent_job(job_id: str, payload: dict[str, Any]) -> None:
    job = JOB_STORE.setdefault(
        job_id,
        {
            "jobId": job_id,
            "status": "queued",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "result": None,
            "error": None,
        },
    )

    try:
        job["status"] = "running"
        job["updatedAt"] = datetime.now(timezone.utc).isoformat()

        import httpx

        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(
                TARGET_AGENT_URL,
                json={
                    "prompt": payload["prompt"],
                    "walletAddress": payload["address"],
                    "chainId": payload["chainId"],
                    "nonce": payload["nonce"],
                    "signature": payload["signature"],
                    "batchCount": payload["batchCount"],
                    "batchSize": payload.get("batchSize", 100),
                },
                headers={
                    "Authorization": f"Bearer {AGENT_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

        job["status"] = "completed"
        job["result"] = data
        job["updatedAt"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:  # pragma: no cover
        job["status"] = "failed"
        job["error"] = str(exc)
        job["updatedAt"] = datetime.now(timezone.utc).isoformat()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return base_templates.TemplateResponse(request, "index.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    try:
        chain_id = web3.eth.chain_id if RPC_CONNECTED else CHAIN_ID
    except Exception:  # pragma: no cover
        chain_id = CHAIN_ID

    return {
        "status": "ok",
        "rpcConnected": RPC_CONNECTED,
        "chainId": chain_id,
        "rpc": RPC_URL,
    }


@app.post("/api/wallet/balance")
async def wallet_balance(payload: WalletAddressRequest) -> dict[str, Any]:
    if not RPC_CONNECTED:
        raise HTTPException(status_code=503, detail="Web3 RPC is not connected. Configure WEB3_RPC_URL in .env to enable on-chain balance reads.")

    try:
        balance_wei = web3.eth.get_balance(payload.address)
        balance_eth = web3.from_wei(balance_wei, "ether")
        return {"address": payload.address, "balance": f"{balance_eth} ETH"}
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=f"Failed to read balance: {str(exc)}") from exc


@app.post("/api/auth/challenge")
async def auth_challenge(payload: ChallengeRequest) -> dict[str, Any]:
    nonce = f"agent-auth:{payload.address}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    issued_at = datetime.now(timezone.utc)
    SessionStore.put(payload.address, nonce, issued_at)

    typed_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "AuthRequest": [
                {"name": "address", "type": "address"},
                {"name": "nonce", "type": "string"},
                {"name": "issuedAt", "type": "string"},
            ],
        },
        "primaryType": "AuthRequest",
        "domain": {
            "name": APP_NAME,
            "version": "1",
            "chainId": payload.chainId,
            "verifyingContract": "0x0000000000000000000000000000000000000000",
        },
        "message": {
            "address": payload.address,
            "nonce": nonce,
            "issuedAt": issued_at.isoformat(),
        },
    }

    return {"address": payload.address, "nonce": nonce, "typedData": typed_data}


@app.post("/api/auth/verify")
async def auth_verify(payload: AuthVerifyRequest) -> dict[str, Any]:
    stored = SessionStore.get(payload.address)
    if not stored:
        raise HTTPException(status_code=401, detail="Challenge not found or expired")

    expected_nonce = stored["nonce"]
    if payload.nonce != expected_nonce:
        raise HTTPException(status_code=401, detail="Invalid nonce")

    typed_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "AuthRequest": [
                {"name": "address", "type": "address"},
                {"name": "nonce", "type": "string"},
                {"name": "issuedAt", "type": "string"},
            ],
        },
        "primaryType": "AuthRequest",
        "domain": {
            "name": APP_NAME,
            "version": "1",
            "chainId": payload.chainId,
            "verifyingContract": "0x0000000000000000000000000000000000000000",
        },
        "message": {
            "address": payload.address,
            "nonce": expected_nonce,
            "issuedAt": stored["issued_at"].isoformat(),
        },
    }

    signer = Account.recover_message(
        encode_typed_data(full_message=typed_data),
        signature=payload.signature,
    )

    recovered = Web3.to_checksum_address(signer)
    if recovered.lower() != payload.address.lower():
        raise HTTPException(status_code=401, detail="Signature verification failed")

    stored["verified_at"] = datetime.now(timezone.utc)
    SessionStore.put(payload.address, expected_nonce, stored["issued_at"])
    return {"address": payload.address, "verified": True, "signature": payload.signature}


@app.post("/api/agent/trigger")
async def trigger_agent(payload: TriggerRequest) -> dict[str, Any]:
    stored = SessionStore.get(payload.address)
    if not stored or payload.nonce != stored["nonce"]:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    if not payload.signature:
        raise HTTPException(status_code=401, detail="Signature required")

    if not TARGET_AGENT_URL:
        return {
            "status": "simulated",
            "address": payload.address,
            "prompt": payload.prompt,
            "message": "No target agent URL configured. This is a secure mock trigger for local testing.",
            "txHash": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    job_id = str(uuid.uuid4())
    JOB_STORE[job_id] = {
        "jobId": job_id,
        "status": "queued",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "result": None,
        "error": None,
    }
    asyncio.create_task(run_agent_job(job_id, payload.model_dump()))

    return {
        "status": "queued",
        "jobId": job_id,
        "address": payload.address,
        "prompt": payload.prompt,
        "message": "Batch submission is running in the background. Poll the job status until it completes.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/agent/jobs/{job_id}")
async def get_agent_job(job_id: str) -> dict[str, Any]:
    job = JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


LAST_RUN_FILE = os.path.join(os.path.dirname(__file__), "last_run.json")
AGENT_TRIGGER_TOKEN = os.getenv("AGENT_TRIGGER_TOKEN", "local-dev-key")
DEFAULT_RECIPIENT = os.getenv("TARGET_ADDRESS", "0xa93065aeedce32b5792fddcc0c075e812bb01617")
DEFAULT_AMOUNT_MTV = float(os.getenv("AMOUNT_MTV", "0.0001"))


_price_cache: dict[str, Any] = {"usd": None, "fetched_at": 0.0, "source": None}


async def get_native_price_usd() -> dict[str, Any]:
    """Return the native token price in USD from CoinGecko, cached in-process.

    Falls back to the last known price (flagged stale) if a refresh fails, and
    to a null price if CoinGecko has never answered this process.
    """
    now = time.time()
    age = now - _price_cache["fetched_at"]
    if _price_cache["usd"] is not None and age < PRICE_TTL_SECONDS:
        return {
            "usd": _price_cache["usd"],
            "source": _price_cache["source"],
            "stale": False,
            "ageSeconds": round(age, 1),
            "error": None,
        }

    try:
        import httpx

        params = {"ids": COINGECKO_ID, "vs_currencies": "usd"}
        headers = {"accept": "application/json"}
        if COINGECKO_API_KEY:
            headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(COINGECKO_PRICE_URL, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        usd = data.get(COINGECKO_ID, {}).get("usd")
        if usd is None:
            raise ValueError(
                f"CoinGecko returned no USD price for id '{COINGECKO_ID}'"
            )
        _price_cache.update({"usd": float(usd), "fetched_at": now, "source": "coingecko"})
        return {"usd": float(usd), "source": "coingecko", "stale": False, "ageSeconds": 0.0, "error": None}
    except Exception as exc:  # network error, rate limit, bad id, ...
        if _price_cache["usd"] is not None:
            return {
                "usd": _price_cache["usd"],
                "source": _price_cache["source"],
                "stale": True,
                "ageSeconds": round(now - _price_cache["fetched_at"], 1),
                "error": str(exc),
            }
        return {"usd": None, "source": None, "stale": True, "ageSeconds": None, "error": str(exc)}


def _compute_fee_summary(run: dict[str, Any], price_info: dict[str, Any]) -> dict[str, Any]:
    symbol = run.get("nativeSymbol") or NATIVE_SYMBOL
    gas_per_tx = int(run.get("gasPerTx") or GAS_PER_TX_FALLBACK)
    tx_count = int(run.get("feeTxCount") or run.get("submitted") or 0)
    gas_price_wei_raw = run.get("gasPriceWei")

    total_fee_wei: int | None
    if run.get("totalFeeWei") is not None:
        total_fee_wei = int(run["totalFeeWei"])
    elif gas_price_wei_raw is not None:
        total_fee_wei = tx_count * gas_per_tx * int(gas_price_wei_raw)
    else:
        total_fee_wei = None

    total_fee_native = (
        float(Web3.from_wei(total_fee_wei, "ether")) if total_fee_wei is not None else None
    )
    gas_price_gwei = (
        float(Web3.from_wei(int(gas_price_wei_raw), "gwei"))
        if gas_price_wei_raw is not None
        else None
    )
    price_usd = price_info.get("usd")
    total_fee_usd = (
        total_fee_native * price_usd
        if (total_fee_native is not None and price_usd is not None)
        else None
    )
    fee_per_tx_native = (
        float(Web3.from_wei(gas_per_tx * int(gas_price_wei_raw), "ether"))
        if gas_price_wei_raw is not None
        else None
    )

    return {
        "nativeSymbol": symbol,
        "txCount": tx_count,
        "gasPerTx": gas_per_tx,
        "gasPriceWei": str(gas_price_wei_raw) if gas_price_wei_raw is not None else None,
        "gasPriceGwei": gas_price_gwei,
        "feePerTxNative": fee_per_tx_native,
        "totalFeeWei": str(total_fee_wei) if total_fee_wei is not None else None,
        "totalFeeNative": total_fee_native,
        "priceUsd": price_usd,
        "totalFeeUsd": total_fee_usd,
        "priceSource": price_info.get("source"),
        "priceStale": price_info.get("stale", False),
        "priceAgeSeconds": price_info.get("ageSeconds"),
        "priceError": price_info.get("error"),
    }


def _enrich_batches_with_fees(run: dict[str, Any], price_info: dict[str, Any]) -> None:
    """Add per-batch network fee (native + USD) to each row of run['batches']."""
    batches = run.get("batches")
    if not isinstance(batches, list):
        return

    gas_per_tx = int(run.get("gasPerTx") or GAS_PER_TX_FALLBACK)
    run_gas_price_wei = run.get("gasPriceWei")
    price_usd = price_info.get("usd")

    for batch in batches:
        if not isinstance(batch, dict):
            continue
        fee_wei = batch.get("feeWei")
        if fee_wei is not None:
            fee_wei_int = int(fee_wei)
        else:
            gas_price_wei = batch.get("gasPriceWei") or run_gas_price_wei
            if gas_price_wei is None:
                continue
            fee_tx_count = int(
                batch.get("feeTxCount")
                if batch.get("feeTxCount") is not None
                else (batch.get("sent") or 0) + (batch.get("skipped") or 0)
            )
            fee_wei_int = fee_tx_count * gas_per_tx * int(gas_price_wei)

        fee_native = float(Web3.from_wei(fee_wei_int, "ether"))
        batch["feeNative"] = fee_native
        batch["feeUsd"] = fee_native * price_usd if price_usd is not None else None


@app.get("/api/price")
async def native_price() -> dict[str, Any]:
    info = await get_native_price_usd()
    return {
        "id": COINGECKO_ID,
        "symbol": NATIVE_SYMBOL,
        "usd": info.get("usd"),
        "source": info.get("source"),
        "stale": info.get("stale", False),
        "ageSeconds": info.get("ageSeconds"),
        "error": info.get("error"),
    }


@app.get("/api/agent/last-run")
async def last_run() -> dict[str, Any]:
    try:
        with open(LAST_RUN_FILE) as fh:
            run = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"status": "none", "submitted": 0, "target": 0}

    price_info = await get_native_price_usd()
    run["fees"] = _compute_fee_summary(run, price_info)
    _enrich_batches_with_fees(run, price_info)
    return run


class BatchRunRequest(BaseModel):
    count: int = Field(default=1000, ge=1, le=20000)
    batchSize: int = Field(default=100, ge=1, le=20000)
    dryRun: bool = True
    amountMtv: float | None = None
    recipient: str | None = None


async def run_batch_job(job_id: str, body: dict[str, Any]) -> None:
    job = JOB_STORE[job_id]
    try:
        job["status"] = "running"
        job["updatedAt"] = datetime.now(timezone.utc).isoformat()

        import httpx

        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(
                TARGET_AGENT_URL or "http://127.0.0.1:9000/trigger",
                json=body,
                headers={
                    "Authorization": f"Bearer {AGENT_TRIGGER_TOKEN}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

        job["status"] = "completed"
        job["result"] = data
        job["updatedAt"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:  # pragma: no cover
        job["status"] = "failed"
        job["error"] = str(exc)
        job["updatedAt"] = datetime.now(timezone.utc).isoformat()


@app.post("/api/agent/batch-run")
async def batch_run(payload: BatchRunRequest) -> dict[str, Any]:
    """Trigger a batched native-transfer run directly (no wallet auth).

    Dry run by default: the agent server signs and counts but does not
    broadcast. Set dryRun=false to send real transactions.
    """
    recipient = payload.recipient or DEFAULT_RECIPIENT
    amount = payload.amountMtv if payload.amountMtv is not None else DEFAULT_AMOUNT_MTV
    prompt = f"send {amount} native MTV to {recipient}"

    job_id = str(uuid.uuid4())
    JOB_STORE[job_id] = {
        "jobId": job_id,
        "status": "queued",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "result": None,
        "error": None,
    }
    asyncio.create_task(run_batch_job(job_id, {
        "prompt": prompt,
        "walletAddress": recipient,
        "chainId": CHAIN_ID,
        "nonce": "batch-run",
        "signature": "batch-run",
        "batchCount": payload.count,
        "batchSize": payload.batchSize,
        "dryRun": payload.dryRun,
    }))

    return {
        "status": "queued",
        "jobId": job_id,
        "count": payload.count,
        "batchSize": payload.batchSize,
        "batches": (payload.count + payload.batchSize - 1) // payload.batchSize,
        "dryRun": payload.dryRun,
        "message": "Batch run started. Poll /api/agent/last-run for live progress.",
    }


# ---------------------------------------------------------------------------
# "Run Forever" background batch runner
#
# Stack: FastAPI + a single asyncio.Task on the app event loop, streamed to
# the browser over Server-Sent Events (text/event-stream). There is no
# threading here, so shared state is async-safe by construction: every
# mutation happens inside the loop coroutine or a request handler on the same
# loop, and readers only ever take a snapshot() copy. The blocking web3 work
# lives in the separate agent-server process and is reached only through
# `await httpx.post(...)`, so this app stays responsive while a cycle runs.
# ---------------------------------------------------------------------------


def _iso_in(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0.0, seconds))).isoformat()


class ForeverRunner:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._guard = asyncio.Lock()  # serialises start/stop
        self._subscribers: set[asyncio.Queue] = set()
        self.cfg: dict[str, Any] = {
            "intervalSeconds": FOREVER_DEFAULT_INTERVAL_SECONDS,
            "count": 1000,
            "batchSize": 100,
            "amountMtv": DEFAULT_AMOUNT_MTV,
            "recipient": DEFAULT_RECIPIENT,
            "dryRun": True,
        }
        self.stats: dict[str, Any] = {}
        self._reset_stats()

    def _reset_stats(self) -> None:
        self._cumulative_fee_mtv = 0.0
        self.stats = {
            "active": False,
            "phase": "idle",  # idle|submitting|waiting|backoff|circuit_open|stopping|stopped
            "startedAt": None,
            "stoppedAt": None,
            "cycles": 0,
            "cyclesSucceeded": 0,
            "cyclesFailed": 0,
            "txSubmitted": 0,
            "txSkipped": 0,
            "txFailed": 0,
            "consecutiveFailures": 0,
            "circuitOpen": False,
            "circuitOpensCount": 0,
            "nextRunAt": None,
            "lastCycleAt": None,
            "lastCycleMs": None,
            "lastError": None,
            "lastResult": None,
            "cumulativeFeeMtv": 0.0,
            "priceUsd": None,
            "priceStale": False,
            "priceSource": None,
            # Per-cycle rows for the incremental "cycle log" data table.
            "cycleLog": [],
            "message": "Idle",
        }

    # ---- cycle log ---------------------------------------------------------
    def _runtime_ms(self) -> int:
        started = self.stats.get("startedAt")
        if not started:
            return 0
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(started)
        return max(0, int(delta.total_seconds() * 1000))

    def _log_cycle_start(self, cycle_no: int) -> dict[str, Any]:
        entry = {
            "cycle": cycle_no,
            "id": f"cycle-{cycle_no}",
            "status": "running",  # running | completed | failed
            "subBatches": None,
            "cycleFeeMtv": 0.0,
            "cumulativeFeeMtv": self._cumulative_fee_mtv,
            "txSubmitted": 0,
            "txFailed": 0,
            "txSkipped": 0,
            "startedAt": datetime.now(timezone.utc).isoformat(),
            "finishedAt": None,
            "cycleMs": None,
            "runtimeMs": self._runtime_ms(),
            "error": None,
        }
        log: list[dict[str, Any]] = self.stats["cycleLog"]
        log.append(entry)
        if len(log) > FOREVER_CYCLE_LOG_MAX:
            del log[: len(log) - FOREVER_CYCLE_LOG_MAX]
        return entry

    async def _refresh_price(self) -> None:
        try:
            info = await get_native_price_usd()
            self.stats["priceUsd"] = info.get("usd")
            self.stats["priceStale"] = bool(info.get("stale"))
            self.stats["priceSource"] = info.get("source")
        except Exception:  # noqa: BLE001 - price is best-effort
            pass

    # ---- SSE pub/sub -----------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=FOREVER_SSE_MAX_QUEUE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def snapshot(self) -> dict[str, Any]:
        stats = dict(self.stats)
        # Deep-ish copy so a slow SSE consumer never serialises a row that is
        # still being mutated by the loop.
        stats["cycleLog"] = [dict(entry) for entry in self.stats.get("cycleLog", [])]
        return {
            **stats,
            "runtimeMs": self._runtime_ms(),
            "config": dict(self.cfg),
            "subscribers": len(self._subscribers),
            "limits": {
                "minIntervalSeconds": FOREVER_MIN_INTERVAL_SECONDS,
                "maxIntervalSeconds": FOREVER_MAX_INTERVAL_SECONDS,
                "maxCycles": FOREVER_MAX_CYCLES,
                "maxConsecutiveFailures": FOREVER_MAX_CONSECUTIVE_FAILURES,
                "circuitCooldownSeconds": FOREVER_CIRCUIT_COOLDOWN_SECONDS,
            },
            "serverTime": datetime.now(timezone.utc).isoformat(),
        }

    def _publish(self) -> None:
        snap = self.snapshot()
        dead: list[asyncio.Queue] = []
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(snap)
            except asyncio.QueueFull:
                # Slow consumer: drop its oldest event and keep the newest.
                try:
                    queue.get_nowait()
                    queue.put_nowait(snap)
                except Exception:  # noqa: BLE001
                    dead.append(queue)
        for queue in dead:
            self._subscribers.discard(queue)

    def _set(self, **updates: Any) -> None:
        self.stats.update(updates)
        self._publish()

    # ---- config -------------------------------------------------------
    def apply_config(self, updates: dict[str, Any]) -> None:
        if not updates:
            return
        interval = updates.get("intervalSeconds")
        if interval is not None:
            self.cfg["intervalSeconds"] = max(
                FOREVER_MIN_INTERVAL_SECONDS,
                min(FOREVER_MAX_INTERVAL_SECONDS, float(interval)),
            )
        for key in ("count", "batchSize"):
            if updates.get(key) is not None:
                self.cfg[key] = max(1, min(20000, int(updates[key])))
        if updates.get("amountMtv") is not None:
            self.cfg["amountMtv"] = float(updates["amountMtv"])
        if updates.get("recipient"):
            self.cfg["recipient"] = str(updates["recipient"])
        if updates.get("dryRun") is not None:
            self.cfg["dryRun"] = bool(updates["dryRun"])

    # ---- lifecycle --------------------------------------------------------
    async def start(self, updates: dict[str, Any]) -> dict[str, Any]:
        async with self._guard:
            self.apply_config(updates)
            if self._task and not self._task.done():
                self._set(message="Config updated; runner already active.")
                return self.snapshot()
            self._reset_stats()
            self.stats.update(
                {
                    "active": True,
                    "phase": "submitting",
                    "startedAt": datetime.now(timezone.utc).isoformat(),
                    "message": "Starting…",
                }
            )
            await self._refresh_price()  # seed a rate for the USD column
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())
            self._publish()
            return self.snapshot()

    async def stop(self, timeout: float = 30.0) -> dict[str, Any]:
        async with self._guard:
            task = self._task
            if not (task and not task.done()):
                self._set(active=False, phase="stopped", nextRunAt=None,
                          message="Runner is not active.")
                return self.snapshot()
            self._set(phase="stopping", message="Stop requested; finishing current cycle…")
            self._stop.set()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            self._set(
                active=False,
                phase="stopped",
                stoppedAt=datetime.now(timezone.utc).isoformat(),
                nextRunAt=None,
                message="Stopped.",
            )
            return self.snapshot()

    async def _sleep_or_stop(self, seconds: float) -> bool:
        """Sleep up to `seconds`; return True if a stop was requested meanwhile."""
        if seconds <= 0:
            return self._stop.is_set()
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def _run_cycle(self) -> dict[str, Any]:
        amount = self.cfg["amountMtv"]
        recipient = self.cfg["recipient"]
        body = {
            "prompt": f"send {amount} native MTV to {recipient}",
            "walletAddress": recipient,
            "chainId": CHAIN_ID,
            "nonce": "forever-runner",
            "signature": "forever-runner",
            "batchCount": self.cfg["count"],
            "batchSize": self.cfg["batchSize"],
            "dryRun": self.cfg["dryRun"],
        }
        import httpx

        async with httpx.AsyncClient(timeout=FOREVER_CYCLE_TIMEOUT_SECONDS) as client:
            response = await client.post(
                TARGET_AGENT_URL or "http://127.0.0.1:9000/trigger",
                json=body,
                headers={
                    "Authorization": f"Bearer {AGENT_TRIGGER_TOKEN}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            return response.json()

    async def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                # Circuit breaker: after N consecutive failures, cool down.
                if self.stats["consecutiveFailures"] >= FOREVER_MAX_CONSECUTIVE_FAILURES:
                    self._set(
                        circuitOpen=True,
                        phase="circuit_open",
                        circuitOpensCount=self.stats["circuitOpensCount"] + 1,
                        nextRunAt=_iso_in(FOREVER_CIRCUIT_COOLDOWN_SECONDS),
                        message=(
                            f"Circuit open after {self.stats['consecutiveFailures']} "
                            f"consecutive failures; cooling down "
                            f"{FOREVER_CIRCUIT_COOLDOWN_SECONDS:g}s."
                        ),
                    )
                    if await self._sleep_or_stop(FOREVER_CIRCUIT_COOLDOWN_SECONDS):
                        break
                    self._set(circuitOpen=False, consecutiveFailures=0,
                              message="Circuit reset; resuming.")

                self.stats["cycles"] += 1
                cycle_no = self.stats["cycles"]
                entry = self._log_cycle_start(cycle_no)
                await self._refresh_price()
                self._set(phase="submitting", nextRunAt=None,
                          message=f"Submitting cycle #{cycle_no}…")
                started = time.perf_counter()
                try:
                    result = await self._run_cycle()
                except Exception as exc:  # noqa: BLE001 - the loop must survive
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    failures = self.stats["consecutiveFailures"] + 1
                    backoff = min(
                        FOREVER_BACKOFF_CAP_SECONDS,
                        FOREVER_BACKOFF_BASE_SECONDS * (2 ** (failures - 1)),
                    )
                    backoff += random.uniform(0, backoff * 0.25)  # jitter
                    # Record the failure as its own row without breaking the log.
                    entry.update({
                        "status": "failed",
                        "finishedAt": datetime.now(timezone.utc).isoformat(),
                        "cycleMs": elapsed_ms,
                        "runtimeMs": self._runtime_ms(),
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    self._set(
                        phase="backoff",
                        cyclesFailed=self.stats["cyclesFailed"] + 1,
                        consecutiveFailures=failures,
                        lastCycleAt=datetime.now(timezone.utc).isoformat(),
                        lastCycleMs=elapsed_ms,
                        lastError=f"{type(exc).__name__}: {exc}",
                        nextRunAt=_iso_in(backoff),
                        message=(
                            f"Cycle #{cycle_no} failed ({failures} in a row); "
                            f"retrying in {backoff:.1f}s."
                        ),
                    )
                    if await self._sleep_or_stop(backoff):
                        break
                    continue

                elapsed_ms = int((time.perf_counter() - started) * 1000)
                sent = int(result.get("transactionCount") or 0)
                skipped = int(result.get("skippedCount") or 0)
                failed = int(result.get("failedCount") or 0)
                cycle_fee_mtv = float(result.get("totalFeeMtv") or 0.0)
                self._cumulative_fee_mtv += cycle_fee_mtv
                entry.update({
                    "status": "completed",
                    "subBatches": result.get("batchCount"),
                    "cycleFeeMtv": cycle_fee_mtv,
                    "cumulativeFeeMtv": self._cumulative_fee_mtv,
                    "txSubmitted": sent,
                    "txFailed": failed,
                    "txSkipped": skipped,
                    "finishedAt": datetime.now(timezone.utc).isoformat(),
                    "cycleMs": elapsed_ms,
                    "runtimeMs": self._runtime_ms(),
                })
                self._set(
                    phase="waiting",
                    cyclesSucceeded=self.stats["cyclesSucceeded"] + 1,
                    consecutiveFailures=0,
                    circuitOpen=False,
                    txSubmitted=self.stats["txSubmitted"] + sent,
                    txSkipped=self.stats["txSkipped"] + skipped,
                    txFailed=self.stats["txFailed"] + failed,
                    cumulativeFeeMtv=self._cumulative_fee_mtv,
                    lastCycleAt=datetime.now(timezone.utc).isoformat(),
                    lastCycleMs=elapsed_ms,
                    lastError=None,
                    lastResult={
                        "transactionCount": sent,
                        "skippedCount": skipped,
                        "failedCount": failed,
                        "batchCount": result.get("batchCount"),
                        "totalTimeSeconds": result.get("totalTimeSeconds"),
                        "totalFeeMtv": result.get("totalFeeMtv"),
                        "dryRun": result.get("dryRun", self.cfg["dryRun"]),
                        "message": result.get("message"),
                    },
                    message=(
                        f"Cycle #{cycle_no} ok: {sent} submitted"
                        + (f", {failed} failed" if failed else "")
                        + (f", {skipped} already known" if skipped else "")
                    ),
                )

                if FOREVER_MAX_CYCLES and cycle_no >= FOREVER_MAX_CYCLES:
                    self._set(message=f"Reached max cycles ({FOREVER_MAX_CYCLES}); stopping.")
                    break

                wait = self.cfg["intervalSeconds"]
                self._set(phase="waiting", nextRunAt=_iso_in(wait),
                          message=f"Waiting {wait:g}s until next cycle…")
                if await self._sleep_or_stop(wait):
                    break
        except asyncio.CancelledError:
            self.stats["message"] = "Stopped (cancelled)."
            raise
        finally:
            self.stats.update(
                {
                    "active": False,
                    "phase": "stopped",
                    "stoppedAt": datetime.now(timezone.utc).isoformat(),
                    "nextRunAt": None,
                }
            )
            if not str(self.stats.get("message", "")).startswith(
                ("Stopped", "Reached max", "Runner is not")
            ):
                self.stats["message"] = "Runner stopped."
            self._publish()


forever_runner = ForeverRunner()


class ForeverStartRequest(BaseModel):
    intervalSeconds: float | None = Field(default=None, ge=0.5, le=3600)
    count: int | None = Field(default=None, ge=1, le=20000)
    batchSize: int | None = Field(default=None, ge=1, le=20000)
    amountMtv: float | None = None
    recipient: str | None = None
    dryRun: bool | None = None


def _sse_pack(obj: dict[str, Any]) -> str:
    return f"event: forever\ndata: {json.dumps(obj)}\n\n"


@app.get("/api/forever/status")
async def forever_status() -> dict[str, Any]:
    return forever_runner.snapshot()


@app.post("/api/forever/start")
async def forever_start(payload: ForeverStartRequest) -> dict[str, Any]:
    return await forever_runner.start(payload.model_dump(exclude_none=True))


@app.post("/api/forever/config")
async def forever_update_config(payload: ForeverStartRequest) -> dict[str, Any]:
    forever_runner.apply_config(payload.model_dump(exclude_none=True))
    forever_runner._publish()
    return forever_runner.snapshot()


@app.post("/api/forever/stop")
async def forever_stop() -> dict[str, Any]:
    return await forever_runner.stop()


@app.get("/api/forever/stream")
async def forever_stream(request: Request) -> StreamingResponse:
    queue = forever_runner.subscribe()

    async def event_gen():
        try:
            yield _sse_pack(forever_runner.snapshot())
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(
                        queue.get(), timeout=FOREVER_SSE_KEEPALIVE_SECONDS
                    )
                    yield _sse_pack(data)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            forever_runner.unsubscribe(queue)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.on_event("shutdown")
async def _forever_shutdown() -> None:
    await forever_runner.stop(timeout=5.0)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
