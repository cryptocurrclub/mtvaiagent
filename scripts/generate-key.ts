import { randomBytes } from "node:crypto";

const key = `0x${randomBytes(32).toString("hex")}`;
console.log("Generated private key:");
console.log(key);
console.log("");
console.log("Store it in DEPLOYER_PRIVATE_KEY and keep it secret.");
console.log("Never commit it to source control or log it in a shared environment.");
