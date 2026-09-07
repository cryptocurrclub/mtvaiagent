import "dotenv/config";
import { createAiTransferPlan, planToLoopConfig } from "../src/aiAgent";
import { runContinuousSendLoop } from "../src/sendMtv";

async function main() {
  const instruction = process.argv.slice(2).join(" ").trim();
  if (!instruction) {
    throw new Error("Provide an instruction, for example: \"send 0.0001 native MTV to 0x... every 10 seconds\".");
  }

  console.log("Asking the AI planner to produce a constrained transfer plan...");
  const plan = await createAiTransferPlan(instruction);
  const loopConfig = planToLoopConfig(plan);

  console.log("Validated plan:");
  console.log(JSON.stringify({
    recipientAddress: loopConfig.recipientAddress,
    amountMtv: loopConfig.amountMtv,
    tokenType: loopConfig.tokenType,
    gasMode: loopConfig.gasMode,
    cooldownMs: loopConfig.cooldownMs,
    maxRetries: loopConfig.maxRetries,
    retryDelayMs: loopConfig.retryDelayMs,
  }, null, 2));
  console.log("Starting only after this local validation. Stop with Ctrl+C or STOP_LOOP=1.");

  await runContinuousSendLoop(loopConfig, console);
}

main().catch((error) => {
  console.error("AI agent stopped:");
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
