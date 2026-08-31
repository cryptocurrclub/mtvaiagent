import { expect } from "chai";
import { ethers } from "hardhat";

describe("MTV agent security controls", function () {
  it("requires independent approvals before funding a wallet", async function () {
    const [owner, approver, recipient, stranger] = await ethers.getSigners();
    const treasury = await ethers.deployContract("TreasuryManager", [owner.address, ethers.parseEther("500"), 2]) as any;
    await treasury.setApprover(approver.address, true);
    await owner.sendTransaction({ to: treasury.target, value: ethers.parseEther("1") });

    await expect(treasury.connect(stranger).requestFunding(recipient.address, ethers.parseEther("1"), Math.floor(Date.now() / 1000) + 3600))
      .to.be.revertedWithCustomError(treasury, "Unauthorized");

    const deadline = Math.floor(Date.now() / 1000) + 3600;
    await treasury.requestFunding(recipient.address, ethers.parseEther("0.1"), deadline);
    await treasury.approveFunding(0);
    await expect(treasury.executeFunding(0)).to.be.revertedWithCustomError(treasury, "NotReady");
    await treasury.connect(approver).approveFunding(0);
    await expect(treasury.executeFunding(0)).to.emit(treasury, "FundingExecuted");
  });

  it("prevents replaying an oracle intent", async function () {
    const [owner, agent] = await ethers.getSigners();
    const oracle = await ethers.deployContract("AgentOracle", [owner.address]) as any;
    await oracle.setAgent(agent.address, true);
    const hash = ethers.keccak256(ethers.toUtf8Bytes("fund wallet"));
    const deadline = Math.floor(Date.now() / 1000) + 3600;
    await oracle.connect(agent).submitIntent(hash, 0, agent.address, 0, deadline);
    await oracle.connect(agent).consumeIntent(hash);
    await expect(oracle.connect(agent).consumeIntent(hash)).to.be.revertedWithCustomError(oracle, "IntentAlreadyConsumed");
  });
});
