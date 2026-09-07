# Python Web3 Agent UI

This project provides a FastAPI backend and browser frontend for connecting a MetaMask wallet, authenticating with a signed challenge, and triggering an AI agent through a secure backend workflow.

## Features

- FastAPI REST API for challenge/auth and agent triggers
- MetaMask wallet connection in the browser using `window.ethereum`
- EIP-712 typed-data signature flow for wallet authentication
- Web3.py helpers for network checks and balance reads
- Transaction status and agent response panels in the browser
- Safe server-side validation and signed challenge workflow

## Project structure

```text
python_agent_ui/
├── app.py
├── .env.example
├── requirements.txt
├── README.md
├── static/
│   ├── app.js
│   └── styles.css
└── templates/
    └── index.html
```

## Setup

1. Create a virtual environment:

```bash
python -m venv .venv
. .venv/bin/activate   # Linux/macOS
# or .venv\Scripts\activate  # Windows
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy environment variables:

```bash
cp .env.example .env
```

4. Update `.env` with your values:

```env
HOST=0.0.0.0
PORT=8000
WEB3_RPC_URL=https://mainnet.infura.io/v3/your-project-id
CHAIN_ID=1
TARGET_AGENT_URL=
AGENT_API_KEY=
SESSION_SECRET=change-this-secret
APP_NAME=AI Agent Wallet UI
```

5. Start the app:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

6. Open Chrome and navigate to:

```text
http://localhost:8000
```

## Browser flow

1. Click `Connect Wallet`.
2. MetaMask prompts for account access.
3. The frontend requests a server-side challenge.
4. The browser signs the typed EIP-712 payload with MetaMask.
5. The backend verifies the signature.
6. The user enters a prompt and clicks `Trigger Agent`.
7. The backend validates the request and calls the configured AI endpoint if provided.

## Security notes

- No private keys are stored in the browser or backend.
- Signing is used for wallet authentication.
- The backend verifies typed-data signatures before triggering the agent.
- Use HTTPS in production and a strong `SESSION_SECRET`.
- Prefer a secured reverse proxy in front of FastAPI.

## Production guidance

- Place FastAPI behind nginx or an HTTPS-enabled gateway.
- Use a real RPC provider such as Infura, Alchemy, or QuickNode.
- Validate network IDs and restrict wallet addresses server-side.
- Configure `TARGET_AGENT_URL` to point at the actual AI service endpoint.
