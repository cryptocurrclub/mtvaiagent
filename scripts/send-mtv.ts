import "dotenv/config";
import { sendMtv } from "../src/sendMtv";

async function main() {
  const targetAddress = process.argv[2] ?? process.env.TARGET_ADDRESS;
  const amountMtv = process.argv[3] ?? process.env.AMOUNT_MTV ?? "0.001";

  try {
    const result = await sendMtv({
      rpcUrl: process.env.MULTIVAC_RPC_URL,
      privateKey: process.env.DEPLOYER_PRIVATE_KEY,
      targetAddress,
      amountMtv,
      chainId: Number(process.env.MULTIVAC_CHAIN_ID ?? 0),
    });

    console.log("MTV transfer sent successfully.");
    console.log("Receipt:");
    console.log(JSON.stringify({
      network: result.network,
      chainId: result.chainId,
      from: result.from,
      to: result.to,
      amountMtv: result.amountMtv,
      txHash: result.txHash,
      blockNumber: result.blockNumber,
      status: result.status,
    }, null, 2));
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error("MTV transfer failed:");
    console.error(message);
    process.exit(1);
  }
}

main();
