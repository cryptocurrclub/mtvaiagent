import { expect } from "chai";
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
});
