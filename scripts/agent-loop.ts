import "dotenv/config";
import { runContinuousSendLoop } from "../src/sendMtv";

async function main() {
  const loopConfig = {
    recipientAddress: process.env.TARGET_ADDRESS,
    amountMtv: process.env.AMOUNT_MTV ?? "0.001",
    tokenType: (process.env.TOKEN_TYPE ?? "native").toLowerCase() as "native",
    gasMode: (process.env.GAS_MODE ?? "legacy").toLowerCase() as "legacy" | "dynamic",
    maxRetries: Number(process.env.MAX_RETRIES ?? 3),
    retryDelayMs: Number(process.env.RETRY_DELAY_MS ?? 1000),
    cooldownMs: Number(process.env.COOLDOWN_MS ?? 0),
    stopEnvKey: process.env.STOP_ENV_KEY ?? "STOP_LOOP",
    auditLogPath: process.env.AUDIT_LOG_PATH,
  };

  try {
    console.log("Starting continuous MTV send loop.");
    console.log(`Recipient: ${loopConfig.recipientAddress}`);
    console.log(`Amount: ${loopConfig.amountMtv} MTV`);
    console.log(`Retry policy: ${loopConfig.maxRetries} attempts with ${loopConfig.retryDelayMs}ms delay`);
    console.log(`Stop signal: ${loopConfig.stopEnvKey}=1 or Ctrl+C`);
    await runContinuousSendLoop(loopConfig, console);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error("Continuous send loop failed to start:");
    console.error(message);
    process.exit(1);
  }
}

main();
