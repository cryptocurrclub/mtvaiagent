import * as fs from "node:fs";
import * as path from "node:path";
import { ethers, type JsonRpcProvider, type TransactionRequest } from "ethers";

export type LoopTokenType = "native" | "erc20";
export type GasMode = "dynamic" | "legacy";

export type SendMtvConfig = {
  rpcUrl?: string;
  privateKey?: string;
  targetAddress?: string;
  amountMtv?: string;
  chainId?: number;
  gasMode?: GasMode;
  tokenType?: LoopTokenType;
};

export type SendMtvLoopConfig = {
  recipientAddress: string;
  targetAddress?: string;
  amountMtv: string;
  tokenType: LoopTokenType;
  gasMode: GasMode;
  maxRetries: number;
  retryDelayMs: number;
  cooldownMs: number;
  stopEnvKey: string;
  auditLogPath?: string;
};

export type LoopAuditEntry = {
  timestamp: string;
  txHash: string;
  status: "confirmed" | "retrying" | "failed" | "paused" | "stopped";
  recipientAddress: string;
  amountMtv: string;
  tokenType: LoopTokenType;
  gasMode: GasMode;
  attempt: number;
  error?: string;
  chainId?: number;
};

export type SendMtvResult = {
  network: string;
  chainId: number;
  from: string;
  to: string;
  amountMtv: string;
  txHash: string;
  blockNumber: number;
  status: string;
};

export function requireConfiguredValue(value: string | undefined, name: string): string {
  const normalized = value?.trim();
  if (!normalized) {
    throw new Error(`${name} is required.`);
  }
  return normalized;
}

export function readPrivateKey(env: NodeJS.ProcessEnv = process.env): string {
  const privateKey = requireConfiguredValue(env.DEPLOYER_PRIVATE_KEY, "DEPLOYER_PRIVATE_KEY");
  if (!/^0x[0-9a-fA-F]{64}$/.test(privateKey)) {
    throw new Error("DEPLOYER_PRIVATE_KEY is invalid. Use a 32-byte hex private key without exposing it in logs.");
  }
  return privateKey;
}

export function parseAmountMtv(amountMtv: string): bigint {
  const value = requireConfiguredValue(amountMtv, "AMOUNT_MTV");
  try {
    const parsed = ethers.parseEther(value);
    if (parsed <= 0n) {
      throw new Error("AMOUNT_MTV must be greater than zero.");
    }
    return parsed;
  } catch (error) {
    throw new Error("AMOUNT_MTV is invalid. Use a value like 0.001");
  }
}

export function validateTargetAddress(address: string): string {
  const normalized = requireConfiguredValue(address, "TARGET_ADDRESS");
  if (!ethers.isAddress(normalized)) {
    throw new Error("TARGET_ADDRESS is not a valid Ethereum address.");
  }
  return normalized;
}

export function buildLoopConfig(config: Partial<SendMtvLoopConfig> = {}): SendMtvLoopConfig {
  const recipientAddress = validateTargetAddress(config.recipientAddress ?? config.targetAddress ?? process.env.TARGET_ADDRESS ?? "");
  const amountMtv = config.amountMtv ?? process.env.AMOUNT_MTV ?? "0.001";
  const tokenType = (config.tokenType ?? (process.env.TOKEN_TYPE ?? "native")).toLowerCase() as LoopTokenType;
  const gasMode = (config.gasMode ?? (process.env.GAS_MODE ?? "legacy")).toLowerCase() as GasMode;
  const maxRetries = Number(config.maxRetries ?? Number(process.env.MAX_RETRIES ?? 3));
  const retryDelayMs = Number(config.retryDelayMs ?? Number(process.env.RETRY_DELAY_MS ?? 1000));
  const cooldownMs = Number(config.cooldownMs ?? Number(process.env.COOLDOWN_MS ?? 0));
  const stopEnvKey = config.stopEnvKey ?? process.env.STOP_ENV_KEY ?? "STOP_LOOP";

  if (tokenType !== "native") {
    throw new Error("Only native MTV transfers are supported in the automated loop.");
  }

  if (gasMode !== "dynamic" && gasMode !== "legacy") {
    throw new Error("GAS_MODE must be either 'dynamic' or 'legacy'.");
  }

  if (!Number.isFinite(maxRetries) || maxRetries < 1) {
    throw new Error("MAX_RETRIES must be a positive integer.");
  }

  if (!Number.isFinite(retryDelayMs) || retryDelayMs < 0) {
    throw new Error("RETRY_DELAY_MS must be zero or a positive integer.");
  }

  if (!Number.isFinite(cooldownMs) || cooldownMs < 0) {
    throw new Error("COOLDOWN_MS must be zero or a positive integer.");
  }

  return {
    recipientAddress,
    targetAddress: recipientAddress,
    amountMtv,
    tokenType,
    gasMode,
    maxRetries: Math.trunc(maxRetries),
    retryDelayMs: Math.trunc(retryDelayMs),
    cooldownMs: Math.trunc(cooldownMs),
    stopEnvKey,
    auditLogPath: config.auditLogPath,
  };
}

export function shouldStopLoop(env: NodeJS.ProcessEnv = process.env, stopEnvKey = "STOP_LOOP"): boolean {
  const value = env[stopEnvKey]?.trim().toLowerCase();
  return value === "1" || value === "true" || value === "yes" || value === "stop";
}

export function appendAuditEntry(entry: LoopAuditEntry, logFilePath?: string): string {
  const resolvedPath = logFilePath ?? path.join(process.cwd(), "logs", "mtv-loop-audit.jsonl");
  fs.mkdirSync(path.dirname(resolvedPath), { recursive: true });
  const line = `${JSON.stringify(entry)}\n`;
  fs.appendFileSync(resolvedPath, line, "utf8");
  return resolvedPath;
}

export async function delay(ms: number): Promise<void> {
  if (ms <= 0) {
    return;
  }

  await new Promise((resolve) => setTimeout(resolve, ms));
}

export async function runContinuousSendLoop(
  config: Partial<SendMtvLoopConfig> = {},
  logger: Pick<Console, "log" | "warn" | "error"> = console,
): Promise<void> {
  const loopConfig = buildLoopConfig(config);
  let shouldTerminate = false;
  let iteration = 0;

  const handleStop = (signal?: string): void => {
    shouldTerminate = true;
    logger.warn(`[loop] manual stop requested via ${signal ?? loopConfig.stopEnvKey}; no further sends will be started.`);
  };

  process.on("SIGINT", handleStop);
  process.on("SIGTERM", handleStop);

  try {
    while (!shouldTerminate && !shouldStopLoop(process.env, loopConfig.stopEnvKey)) {
      iteration += 1;
      logger.log(`[loop] iteration ${iteration} sending ${loopConfig.amountMtv} MTV to ${loopConfig.recipientAddress}`);

      let attempt = 0;
      let lastError: Error | null = null;

      while (!shouldTerminate && !shouldStopLoop(process.env, loopConfig.stopEnvKey)) {
        try {
          const result = await sendMtv({
            rpcUrl: process.env.MULTIVAC_RPC_URL,
            privateKey: process.env.DEPLOYER_PRIVATE_KEY,
            targetAddress: loopConfig.recipientAddress,
            amountMtv: loopConfig.amountMtv,
            chainId: Number(process.env.MULTIVAC_CHAIN_ID ?? 0),
            gasMode: loopConfig.gasMode,
            tokenType: loopConfig.tokenType,
          });

          const persisted = appendAuditEntry({
            timestamp: new Date().toISOString(),
            txHash: result.txHash,
            status: "confirmed",
            recipientAddress: result.to,
            amountMtv: result.amountMtv,
            tokenType: loopConfig.tokenType,
            gasMode: loopConfig.gasMode,
            attempt: attempt + 1,
            chainId: result.chainId,
          }, loopConfig.auditLogPath);

          logger.log(`[loop] tx confirmed ${result.txHash} recorded in ${persisted}`);
          break;
        } catch (error) {
          lastError = error instanceof Error ? error : new Error(String(error));
          attempt += 1;
          appendAuditEntry({
            timestamp: new Date().toISOString(),
            txHash: "",
            status: attempt >= loopConfig.maxRetries ? "paused" : "retrying",
            recipientAddress: loopConfig.recipientAddress,
            amountMtv: loopConfig.amountMtv,
            tokenType: loopConfig.tokenType,
            gasMode: loopConfig.gasMode,
            attempt,
            error: lastError.message,
          }, loopConfig.auditLogPath);

          if (attempt >= loopConfig.maxRetries) {
            logger.error(`[loop] maximum retries reached (${loopConfig.maxRetries}); pausing the loop. Set ${loopConfig.stopEnvKey}=0 to resume or stop with Ctrl+C.`);
            shouldTerminate = true;
            break;
          }

          logger.warn(`[loop] attempt ${attempt}/${loopConfig.maxRetries} failed: ${lastError.message}. Retrying in ${loopConfig.retryDelayMs}ms.`);
          await delay(loopConfig.retryDelayMs);
        }
      }

      if (shouldTerminate || shouldStopLoop(process.env, loopConfig.stopEnvKey)) {
        logger.warn("[loop] stop requested or retry budget exhausted; exiting loop.");
        break;
      }

      if (loopConfig.cooldownMs > 0) {
        logger.log(`[loop] waiting ${loopConfig.cooldownMs}ms before next transaction.`);
        await delay(loopConfig.cooldownMs);
      }
    }

    logger.log("[loop] continuous transfer loop has ended.");
  } finally {
    process.off("SIGINT", handleStop);
    process.off("SIGTERM", handleStop);
  }
}

export function resolveNetworkName(chainId: number | undefined): string {
  if (chainId === 62621) return "MultiVAC testnet";
  if (chainId === 1) return "MultiVAC mainnet";
  return "configured MultiVAC network";
}

export async function sendMtv(config: SendMtvConfig): Promise<SendMtvResult> {
  const rpcUrl = requireConfiguredValue(config.rpcUrl ?? process.env.MULTIVAC_RPC_URL, "MULTIVAC_RPC_URL");
  const privateKey = readPrivateKey();
  const targetAddress = validateTargetAddress(config.targetAddress ?? process.env.TARGET_ADDRESS ?? "");
  const amountWei = parseAmountMtv(config.amountMtv ?? process.env.AMOUNT_MTV ?? "0.001");
  const chainId = config.chainId ?? (Number(process.env.MULTIVAC_CHAIN_ID ?? 0) || undefined);
  const tokenType = (config.tokenType ?? (process.env.TOKEN_TYPE ?? "native")).toLowerCase();
  const gasMode = (config.gasMode ?? (process.env.GAS_MODE ?? "legacy")).toLowerCase();

  if (tokenType !== "native") {
    throw new Error("Only native MTV transfers are supported in the current runtime.");
  }

  if (gasMode !== "dynamic" && gasMode !== "legacy") {
    throw new Error("GAS_MODE must be either 'dynamic' or 'legacy'.");
  }

  const provider = new ethers.JsonRpcProvider(rpcUrl);
  const network = await provider.getNetwork();
  const resolvedChainId = Number(network.chainId);
  const wallet = new ethers.Wallet(privateKey, provider);

  const [balance, feeData] = await Promise.all([
    provider.getBalance(wallet.address),
    provider.getFeeData(),
  ]);

  const txRequest: TransactionRequest = {
    from: wallet.address,
    to: targetAddress,
    value: amountWei,
    chainId: chainId ?? resolvedChainId,
  };

  const gasEstimate = await provider.estimateGas(txRequest).catch((error) => {
    throw new Error(`Transaction simulation failed: ${error instanceof Error ? error.message : "unknown error"}`);
  });

  const gasPrice = feeData.gasPrice ?? feeData.maxFeePerGas ?? 1_000_000_000n;
  const maxFeePerGas = feeData.maxFeePerGas ?? gasPrice;
  const maxPriorityFeePerGas = feeData.maxPriorityFeePerGas ?? 0n;
  const gasLimit = gasEstimate + 5000n;
  const totalCost = amountWei + gasLimit * gasPrice;

  if (balance < totalCost) {
    throw new Error("Insufficient balance to cover the transfer value and network fees.");
  }

  const legacyTransaction = {
    ...txRequest,
    gasLimit,
    gasPrice,
  };

  const dynamicFeeTransaction = {
    ...txRequest,
    gasLimit,
    maxFeePerGas,
    maxPriorityFeePerGas,
  };

  let signedTx;
  try {
    if (gasMode === "dynamic") {
      signedTx = await wallet.sendTransaction(dynamicFeeTransaction);
    } else {
      signedTx = await wallet.sendTransaction(legacyTransaction);
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (gasMode === "dynamic" && (message.toLowerCase().includes("transaction type not supported") || message.toLowerCase().includes("not supported"))) {
      signedTx = await wallet.sendTransaction(legacyTransaction);
    } else {
      throw error;
    }
  }

  console.log(`Broadcasting MTV transfer from ${wallet.address} to ${targetAddress}`);
  console.log(`Transaction hash: ${signedTx.hash}`);

  const receipt = await provider.waitForTransaction(signedTx.hash, 1, 120000).catch((error) => {
    throw new Error(`Transaction confirmation failed: ${error instanceof Error ? error.message : "unknown error"}`);
  });

  if (!receipt || receipt.status === 0) {
    throw new Error("Transaction broadcast failed or was reverted.");
  }

  return {
    network: resolveNetworkName(chainId ?? resolvedChainId),
    chainId: chainId ?? resolvedChainId,
    from: wallet.address,
    to: targetAddress,
    amountMtv: ethers.formatEther(amountWei),
    txHash: signedTx.hash,
    blockNumber: Number(receipt.blockNumber ?? 0),
    status: "confirmed",
  };
}
