import "dotenv/config";
import { ethers } from "ethers";

function isValidPrivateKey(value: string | undefined): boolean {
  return typeof value === "string" && /^0x[0-9a-fA-F]{64}$/.test(value.trim());
}

const key = process.env.DEPLOYER_PRIVATE_KEY;
if (!isValidPrivateKey(key)) {
  console.error("Invalid DEPLOYER_PRIVATE_KEY.");
  console.error("Expected 32-byte hex private key like: 0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef");
  process.exit(1);
}

const wallet = new ethers.Wallet(key!);
console.log("DEPLOYER_PRIVATE_KEY is valid.");
console.log(`Wallet address: ${wallet.address}`);
