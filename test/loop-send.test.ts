import { expect } from "chai";
import { createAiTransferPlan, parseInstructionFallback, planToLoopConfig, validateAiTransferPlan } from "../src/aiAgent";
import { buildLoopConfig, shouldStopLoop } from "../src/sendMtv";

describe("MTV send loop configuration", function () {
  it("keeps key transaction values consistent across iterations", function () {
    const config = buildLoopConfig({
      targetAddress: "0x1234567890123456789012345678901234567890",
      amountMtv: "0.25",
      maxRetries: 3,
      cooldownMs: 5000,
      retryDelayMs: 1000,
      tokenType: "native",
      gasMode: "legacy",
      recipientAddress: "0x1234567890123456789012345678901234567890",
    });

    expect(config.amountMtv).to.equal("0.25");
    expect(config.recipientAddress).to.equal("0x1234567890123456789012345678901234567890");
    expect(config.tokenType).to.equal("native");
    expect(config.gasMode).to.equal("legacy");
    expect(config.maxRetries).to.equal(3);
  });

  it("stops the loop only when the operator requests it", function () {
    expect(shouldStopLoop({ STOP_LOOP: "0" })).to.equal(false);
    expect(shouldStopLoop({ STOP_LOOP: "1" })).to.equal(true);
    expect(shouldStopLoop({})).to.equal(false);
  });

  it("accepts only a locally validated native MTV AI plan", function () {
    const plan = validateAiTransferPlan({
      recipientAddress: "0x1234567890123456789012345678901234567890",
      amountMtv: "0.01",
      tokenType: "native",
      gasMode: "legacy",
      cooldownMs: 1000,
      maxRetries: 2,
      retryDelayMs: 500,
    });

    expect(planToLoopConfig(plan).amountMtv).to.equal("0.01");
    expect(() => validateAiTransferPlan({ ...plan, tokenType: "erc20" })).to.throw("only plan native MTV");
  });

  it("normalizes alternate AI field names like gas and to before validation", function () {
    const plan = validateAiTransferPlan({
      to: "0x1234567890123456789012345678901234567890",
      amount: "0.01",
      token: "native",
      gas: "legacy",
      cooldownMs: 1000,
      retries: 2,
      retryDelay: 500,
    });

    expect(plan.recipientAddress).to.equal("0x1234567890123456789012345678901234567890");
    expect(plan.gasMode).to.equal("legacy");
    expect(plan.maxRetries).to.equal(2);
  });

  it("defaults a missing gasMode to legacy rather than failing the AI plan", function () {
    const plan = validateAiTransferPlan({
      recipientAddress: "0x1234567890123456789012345678901234567890",
      amountMtv: "0.01",
      tokenType: "native",
      cooldownMs: 1000,
      maxRetries: 2,
      retryDelayMs: 500,
    });

    expect(plan.gasMode).to.equal("legacy");
  });

  it("accepts string-based numeric values returned by the AI model", function () {
    const plan = validateAiTransferPlan({
      recipientAddress: "0x1234567890123456789012345678901234567890",
      amountMtv: "0.01",
      tokenType: "native",
      gasMode: "legacy",
      cooldownMs: "1000",
      maxRetries: "2",
      retryDelayMs: "500",
    });

    expect(plan.cooldownMs).to.equal(1000);
    expect(plan.maxRetries).to.equal(2);
    expect(plan.retryDelayMs).to.equal(500);
  });

  it("accepts numeric amount values returned by the AI model", function () {
    const plan = validateAiTransferPlan({
      recipientAddress: "0x1234567890123456789012345678901234567890",
      amountMtv: 0.01,
      tokenType: "native",
      gasMode: "legacy",
      cooldownMs: 1000,
      maxRetries: 2,
      retryDelayMs: 500,
    });

    expect(plan.amountMtv).to.equal("0.01");
  });

  it("applies safe defaults when the AI omits or nulls numeric plan values", function () {
    const plan = validateAiTransferPlan({
      recipientAddress: "0x1234567890123456789012345678901234567890",
      amountMtv: "0.01",
      tokenType: "native",
      gasMode: "legacy",
      cooldownMs: null,
      maxRetries: null,
      retryDelayMs: "null",
    });

    expect(plan.cooldownMs).to.equal(10000);
    expect(plan.maxRetries).to.equal(3);
    expect(plan.retryDelayMs).to.equal(1000);
  });

  it("retries the AI request after a rate-limit response instead of giving up", async function () {
    const originalFetch = globalThis.fetch;
    let calls = 0;
    globalThis.fetch = (async () => {
      calls += 1;
      if (calls === 1) {
        return { ok: false, status: 429, headers: new Headers({ "retry-after": "0" }) } as Response;
      }

      return {
        ok: true,
        json: async () => ({
          choices: [{ message: { content: JSON.stringify({
            recipientAddress: "0x1234567890123456789012345678901234567890",
            amountMtv: "0.0001",
            tokenType: "native",
            gasMode: "legacy",
            cooldownMs: 10000,
            maxRetries: 2,
            retryDelayMs: 500,
          }) } }]
        }),
      } as Response;
    }) as typeof fetch;

    try {
      const plan = await createAiTransferPlan("send 0.0001 native MTV to 0x1234567890123456789012345678901234567890 every 10 seconds", { OPENAI_API_KEY: "demo-key", OPENAI_MAX_RETRIES: "2" });
      expect(plan.amountMtv).to.equal("0.0001");
      expect(plan.recipientAddress).to.equal("0x1234567890123456789012345678901234567890");
      expect(calls).to.equal(2);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
