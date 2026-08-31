import { ethers, type JsonRpcProvider, type TransactionRequest } from "ethers";

export type SendMtvConfig = {
  rpcUrl?: string;
  privateKey?: string;
  targetAddress?: string;
  amountMtv?: string;
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
    signedTx = await wallet.sendTransaction(dynamicFeeTransaction);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.toLowerCase().includes("transaction type not supported") || message.toLowerCase().includes("not supported")) {
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
