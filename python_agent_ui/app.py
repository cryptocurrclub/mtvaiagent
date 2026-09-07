import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from eth_account import Account
from eth_account.messages import encode_typed_data
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
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
    return base_templates.TemplateResponse("index.html", {"request": request})


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


@app.get("/api/agent/last-run")
async def last_run() -> dict[str, Any]:
    try:
        with open(LAST_RUN_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"status": "none", "submitted": 0, "target": 0}


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
