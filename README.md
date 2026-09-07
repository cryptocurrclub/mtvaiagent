# MTV AI Agent

This project is a constrained AI-assisted MultiVAC transaction agent for sending native MTV from a configured wallet to a recipient address. The AI interprets a natural-language instruction into a structured plan; deterministic local validation and the transaction loop control what can actually be sent.

The AI does not receive the private key and cannot directly sign or submit transactions. It may only propose native MTV transfer parameters. The local runtime validates that plan before invoking the transaction loop.

## Overview

The runtime flow is intentionally simple and explicit:

1. Load configuration from environment variables.
2. Validate the private key format and target address format.
3. Connect to the configured MultiVAC RPC endpoint.
4. Check the wallet balance and estimate the transfer fee.
5. Reject the transaction if the wallet does not have enough funds.
6. Broadcast the transaction using a compatible transaction type.
7. Wait for confirmation and print the receipt information.

This project is designed around security and operational clarity rather than multi-step autonomous orchestration.

## Current functionality

The actual code currently supports:

- native MTV transfer from a configured wallet
- target address validation using EIP-55-compliant address checks
- secure private-key loading from environment config
- fee estimation and gas limit calculation
- fallback from dynamic-fee transactions to legacy `gasPrice` transactions for networks that reject typed transactions
- receipt monitoring and confirmation status reporting
- clear error messages for invalid configuration, invalid addresses, insufficient balance, and RPC failures
- natural-language planning through an OpenAI-compatible chat-completions endpoint
- strict local validation that rejects unsupported token types and malformed AI plans
- continuous execution with retry limits, cooldowns, operator stop controls, and JSONL audit logging

## Architecture

### Runtime flow

The real implementation lives in the following files:

- `src/sendMtv.ts` — core send logic, validation, fee estimation, transaction creation, confirmation handling
- `scripts/send-mtv.ts` — CLI entry point that reads env/config values and executes a transfer
- `scripts/ai-agent.ts` — AI planning entry point followed by local validation and continuous execution
- `src/aiAgent.ts` — model request, structured plan parsing, and policy validation
- `scripts/generate-key.ts` — generates a new wallet private key for use with `DEPLOYER_PRIVATE_KEY`
- `scripts/validate-key.ts` — validates a configured private key before sending
- `docs/env.production.example` — production-ready environment template

### Control loop

The logic in `sendMtv()` is the effective execution loop:

```ts
export async function sendMtv(config: SendMtvConfig): Promise<SendMtvResult> {
  const rpcUrl = requireConfiguredValue(config.rpcUrl ?? process.env.MULTIVAC_RPC_URL, "MULTIVAC_RPC_URL");
  const privateKey = readPrivateKey();
  const targetAddress = validateTargetAddress(config.targetAddress ?? process.env.TARGET_ADDRESS ?? "");
  const amountWei = parseAmountMtv(config.amountMtv ?? process.env.AMOUNT_MTV ?? "0.001");
  const chainId = config.chainId ?? (Number(process.env.MULTIVAC_CHAIN_ID ?? 0) || undefined);

  const provider = new ethers.JsonRpcProvider(rpcUrl);
  const network = await provider.getNetwork();
  const wallet = new ethers.Wallet(privateKey, provider);

  const [balance, feeData] = await Promise.all([
    provider.getBalance(wallet.address),
    provider.getFeeData(),
  ]);

  const gasEstimate = await provider.estimateGas(txRequest);
  const gasLimit = gasEstimate + 5000n;
  const totalCost = amountWei + gasLimit * (feeData.gasPrice ?? feeData.maxFeePerGas ?? 1_000_000_000n);

  if (balance < totalCost) {
    throw new Error("Insufficient balance to cover the transfer value and network fees.");
  }

  // Try dynamic-fee tx first; fallback to legacy if the RPC rejects it.
  // Wait for receipt and return confirmation data.
}
```

### Legacy contracts in the repo

The repository still contains Solidity contracts under `contracts/` such as `TreasuryManager.sol`, `AgentOracle.sol`, `CrossShardGateway.sol`, and `LiquidityRouter.sol`. Those files are part of an earlier or broader design; the active runtime uses the native MTV sender and the constrained AI planner.

## Configuration

Create a `.env` file from the example template or set the values in your shell before running the tool.

### Required environment variables

| Variable | Required | Description |
| --- | --- | --- |
| `MULTIVAC_RPC_URL` | Yes | RPC endpoint for the target MultiVAC network |
| `MULTIVAC_CHAIN_ID` | Yes | Chain ID for the target network, e.g. `62621` |
| `DEPLOYER_PRIVATE_KEY` | Yes | Private key for the wallet sending MTV; must be a 32-byte hex key prefixed with `0x` |
| `TARGET_ADDRESS` | Yes for config mode | Recipient address for the transfer |
| `AMOUNT_MTV` | Yes for config mode | Amount in MTV, e.g. `0.001` |

### Example environment file

```env
MULTIVAC_RPC_URL=https://rpc.mtv.ac
MULTIVAC_CHAIN_ID=62621
DEPLOYER_PRIVATE_KEY=0xYOUR_32_BYTE_PRIVATE_KEY
TARGET_ADDRESS=0xYOUR_RECIPIENT_ADDRESS
AMOUNT_MTV=0.001
```

See `docs/env.production.example` for a more complete production template.

## Setup and installation

### 1. Install dependencies

```bash
npm install
```

### 2. Recommended Node version

This project currently uses Hardhat 2.19.5 and Node 22 can emit warnings. For the most stable setup, use Node 20 LTS.

```bash
nvm install 20
nvm use 20
npm install
```

### 3. Generate a private key

Use the helper script to generate a fresh private key safely:

```bash
npm run keys:generate
```

This prints a new key that you should store in a secure environment variable or secret manager. Do not commit it to version control.

### 4. Validate the key

```bash
npm run keys:validate
```

This checks that the key is in the required format and derives the wallet address without exposing the key value itself.

## Usage

### Run the continuous sender

Set the values in a `.env` file and run:

```bash
npm run agent
```

This starts the continuous sender. It sends one confirmed transaction, then starts the next identical transaction. Stop it with `Ctrl+C` or by setting `STOP_LOOP=1` in the process environment. Use `npm run agent:send` for a single transaction.

### Send one transaction

If `TARGET_ADDRESS` and `AMOUNT_MTV` are set in the environment, the single-send script uses those values. Otherwise, pass them as arguments.

### Send MTV with explicit arguments

```bash
npx ts-node scripts/send-mtv.ts 0xYourRecipientAddress 0.001
```

This command will:

- load `MULTIVAC_RPC_URL` and `DEPLOYER_PRIVATE_KEY` from `.env`
- validate the target address
- parse the amount
- estimate the transaction fee
- confirm the wallet has enough balance
- send the transaction
- wait for receipt confirmation and print the final status

### Usage examples

#### Example 1: send a fixed amount from `.env`

```env
MULTIVAC_RPC_URL=https://rpc.mtv.ac
MULTIVAC_CHAIN_ID=62621
DEPLOYER_PRIVATE_KEY=0xYOUR_32_BYTE_PRIVATE_KEY
TARGET_ADDRESS=0x1234567890123456789012345678901234567890
AMOUNT_MTV=0.25
```

```bash
npm run agent
```

#### Example 2: send a one-off transfer from the command line

```bash
npx ts-node scripts/send-mtv.ts 0x1234567890123456789012345678901234567890 0.25
```

#### Example 3: validate your key before sending

```bash
npm run keys:validate
```

## Decision-making flow / control loop

The actual send flow is implemented as a single, deterministic control loop:

1. Load required values from `process.env`.
2. Validate the private key format using a strict regex: `^0x[0-9a-fA-F]{64}$`.
3. Validate the target address with `ethers.isAddress()`.
4. Convert the amount to wei with `ethers.parseEther()`.
5. Connect to the RPC using `new ethers.JsonRpcProvider(rpcUrl)`.
6. Retrieve wallet balance and current fee data.
7. Simulate the transaction with `provider.estimateGas()`.
8. Reject the send if the wallet balance cannot cover amount + gas.
9. Attempt the transaction using dynamic-fee fields first.
10. If the RPC rejects it because typed transactions are unsupported, retry with a legacy `gasPrice` transaction.
11. Wait for the receipt and report status.

The loop keeps transaction execution deterministic after the AI plan has been validated.

## Supported integrations and external APIs

The project integrates with:

- MultiVAC RPC endpoints via `ethers.JsonRpcProvider`
- environment configuration via `dotenv`
- Ethers v6 transaction signing and confirmation logic
- Hardhat for compilation and testing of the Solidity artifacts in `contracts/`

The optional AI runtime uses an OpenAI-compatible chat-completions API only for planning. Wallet signing and transaction submission remain local.

## Security model

This project takes the following security measures:

- private keys are read only from environment variables
- invalid private keys fail before any transaction is sent
- private keys are never printed in logs or error output
- target addresses are validated before sending
- insufficient balance is rejected before broadcast
- network-specific fee compatibility is handled with a fallback mechanism
- AI responses are parsed as structured data and restricted to native MTV transfers
- the AI request never includes `DEPLOYER_PRIVATE_KEY`

## Development notes

- Keep `.env` out of source control.
- Use `docs/env.production.example` as the safe template for production deployment.
- Validate TypeScript before committing changes:

```bash
npx tsc --noEmit
```

- Run the project test suite as needed:

```bash
npm test
```

- Compile the contracts when modifying Solidity artifacts:

```bash
npm run compile
```

## Contribution guidelines

1. Keep the runtime focused on constrained native MTV transfers.
2. Preserve the boundary between AI planning and local signing.
3. Preserve private-key safety rules.
4. Validate all new runtime code with `npx tsc --noEmit`.
5. Update README and env examples whenever configuration or behavior changes.

## Summary

The project is a secure, constrained AI-assisted MTV transfer agent for MultiVAC networks. It uses AI for natural-language planning, then relies on deterministic local validation, signing, retry controls, cooldowns, stop handling, and confirmation monitoring.
