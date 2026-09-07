import { buildLoopConfig, type GasMode, type LoopTokenType, type SendMtvLoopConfig } from "./sendMtv";

export type AiTransferPlan = {
  recipientAddress: string;
  amountMtv: string;
  tokenType: LoopTokenType;
  gasMode: GasMode;
  cooldownMs: number;
  maxRetries: number;
  retryDelayMs: number;
};

type ChatCompletionResponse = {
  choices?: Array<{ message?: { content?: string } }>;
};

function extractJson(content: string): unknown {
  const fenced = content.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
  return JSON.parse(fenced?.[1] ?? content);
}

function requireString(value: unknown, name: string): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value).trim();
  }

  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed) {
      return trimmed;
    }
  }

  throw new Error(`AI plan field '${name}' is missing or invalid.`);
}

function requireNonNegativeInteger(value: unknown, name: string): number {
  const numericValue = typeof value === "string" ? Number(value.trim()) : value;
  if (typeof numericValue !== "number" || !Number.isFinite(numericValue) || !Number.isInteger(numericValue) || numericValue < 0) {
    throw new Error(`AI plan field '${name}' must be a non-negative integer.`);
  }
  return numericValue;
}

function normalizePlanShape(plan: Record<string, unknown>): Record<string, unknown> {
  const normalized: Record<string, unknown> = { ...plan };

  const aliases: Array<[string, string]> = [
    ["gasMode", "gasMode"],
    ["gas_mode", "gasMode"],
    ["gasmode", "gasMode"],
    ["gas", "gasMode"],
    ["tokenType", "tokenType"],
    ["token_type", "tokenType"],
    ["tokentype", "tokenType"],
    ["token", "tokenType"],
    ["recipientAddress", "recipientAddress"],
    ["recipient_address", "recipientAddress"],
    ["recipientaddress", "recipientAddress"],
    ["to", "recipientAddress"],
    ["amountMtv", "amountMtv"],
    ["amount_mtv", "amountMtv"],
    ["amountmtv", "amountMtv"],
    ["amount", "amountMtv"],
    ["maxRetries", "maxRetries"],
    ["max_retries", "maxRetries"],
    ["maxretries", "maxRetries"],
    ["retries", "maxRetries"],
    ["retryDelayMs", "retryDelayMs"],
    ["retry_delay_ms", "retryDelayMs"],
    ["retrydelayms", "retryDelayMs"],
    ["retryDelay", "retryDelayMs"],
  ];

  for (const [sourceKey, targetKey] of aliases) {
    const value = normalized[sourceKey];
    if (value !== undefined && normalized[targetKey] === undefined) {
      normalized[targetKey] = value;
    }
  }

  const defaultNumericValues: Record<string, number> = {
    cooldownMs: 10000,
    maxRetries: 3,
    retryDelayMs: 1000,
  };

  for (const [key, defaultValue] of Object.entries(defaultNumericValues)) {
    const value = normalized[key];
    const isMissing = value === undefined || value === null || value === "" || (typeof value === "string" && value.trim() === "") || (typeof value === "string" && value.trim().toLowerCase() === "null");
    if (isMissing) {
      normalized[key] = defaultValue;
    }
  }

  if (!normalized.gasMode) {
    normalized.gasMode = "legacy";
  }

  return normalized;
}

export function validateAiTransferPlan(value: unknown): AiTransferPlan {
  if (!value || typeof value !== "object") {
    throw new Error("AI returned an invalid transfer plan.");
  }

  const plan = normalizePlanShape(value as Record<string, unknown>);
  const tokenType = requireString(plan.tokenType, "tokenType").toLowerCase() as LoopTokenType;
  const gasMode = requireString(plan.gasMode, "gasMode").toLowerCase() as GasMode;

  if (tokenType !== "native") {
    throw new Error("The AI agent may only plan native MTV transfers.");
  }

  if (gasMode !== "legacy" && gasMode !== "dynamic") {
    throw new Error("AI plan gasMode must be 'legacy' or 'dynamic'.");
  }

  const maxRetries = requireNonNegativeInteger(plan.maxRetries, "maxRetries");
  if (maxRetries < 1) {
    throw new Error("AI plan maxRetries must be at least 1.");
  }

  return {
    recipientAddress: requireString(plan.recipientAddress, "recipientAddress"),
    amountMtv: requireString(plan.amountMtv, "amountMtv"),
    tokenType,
    gasMode,
    cooldownMs: requireNonNegativeInteger(plan.cooldownMs, "cooldownMs"),
    maxRetries,
    retryDelayMs: requireNonNegativeInteger(plan.retryDelayMs, "retryDelayMs"),
  };
}

export function parseInstructionFallback(instruction: string): AiTransferPlan {
  const normalized = instruction.trim();
  if (!normalized) {
    throw new Error("No instruction was provided for the AI agent.");
  }

  const addressMatch = normalized.match(/0x[a-fA-F0-9]{40}\b/);
  const amountMatch = normalized.match(/(\d+(?:\.\d+)?)\s*(?:mtv|token|coins)?/i);
  const secondsMatch = normalized.match(/every\s+(\d+(?:\.\d+)?)\s*(second|seconds|sec|secs)/i);
  const minutesMatch = normalized.match(/every\s+(\d+(?:\.\d+)?)\s*(minute|minutes|min|mins)/i);

  const recipientAddress = addressMatch ? addressMatch[0] : "0x0000000000000000000000000000000000000000";
  const amountMtv = amountMatch ? amountMatch[1] : "0.001";

  if (!/^0x[a-fA-F0-9]{40}$/.test(recipientAddress)) {
    throw new Error("The instruction did not include a valid recipient address for a transfer.");
  }

  let cooldownMs = 10000;
  if (secondsMatch) {
    cooldownMs = Math.round(Number(secondsMatch[1]) * 1000);
  } else if (minutesMatch) {
    cooldownMs = Math.round(Number(minutesMatch[1]) * 60 * 1000);
  }

  return {
    recipientAddress,
    amountMtv,
    tokenType: "native",
    gasMode: "legacy",
    cooldownMs,
    maxRetries: 3,
    retryDelayMs: 1000,
  };
}

function getRetryDelayMs(response: Response | undefined, attempt: number, env: NodeJS.ProcessEnv): number {
  const configuredBaseMs = Number(env.OPENAI_RETRY_DELAY_MS ?? 1000);
  const resetHeader = response?.headers?.get?.("retry-after");
  if (resetHeader) {
    const seconds = Number(resetHeader);
    if (!Number.isNaN(seconds) && seconds > 0) {
      return seconds * 1000;
    }
  }

  return configuredBaseMs * Math.pow(2, attempt);
}

export async function createAiTransferPlan(instruction: string, env: NodeJS.ProcessEnv = process.env): Promise<AiTransferPlan> {
  const apiKey = env.OPENAI_API_KEY?.trim();
  const endpoint = (env.OPENAI_BASE_URL ?? "https://api.openai.com/v1").replace(/\/$/, "") + "/chat/completions";
  const model = env.OPENAI_MODEL?.trim() || "gpt-4o-mini";
  const maxRetries = Number(env.OPENAI_MAX_RETRIES ?? 4);

  if (!apiKey) {
    throw new Error("OPENAI_API_KEY is required for natural-language planning.");
  }

  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          model,
          temperature: 0,
          response_format: { type: "json_object" },
          messages: [
            {
              role: "system",
              content: "You plan MultiVAC transfers only. Return JSON with recipientAddress, amountMtv, tokenType, gasMode, cooldownMs, maxRetries, retryDelayMs. tokenType must be native. Never invent a missing address or amount; return null for missing values.",
            },
            { role: "user", content: instruction },
          ],
        }),
      });

      if (!response.ok) {
        if ((response.status === 429 || response.status >= 500) && attempt < maxRetries) {
          const retryMs = getRetryDelayMs(response, attempt, env);
          console.warn(`AI planning endpoint rate-limited (HTTP ${response.status}); retrying in ${retryMs}ms (${attempt + 1}/${maxRetries}).`);
          await new Promise((resolve) => setTimeout(resolve, retryMs));
          continue;
        }

        throw new Error(`AI planning request failed with HTTP ${response.status}.`);
      }

      const payload = await response.json() as ChatCompletionResponse;
      const content = payload.choices?.[0]?.message?.content;
      if (!content) {
        throw new Error("AI returned no transfer plan.");
      }

      return validateAiTransferPlan(extractJson(content));
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));
      if (attempt < maxRetries && (/429|rate limit|fetch|timed out|network/i.test(lastError.message))) {
        const retryMs = getRetryDelayMs(undefined, attempt, env);
        console.warn(`AI request failed (${lastError.message}); retrying in ${retryMs}ms (${attempt + 1}/${maxRetries}).`);
        await new Promise((resolve) => setTimeout(resolve, retryMs));
        continue;
      }
      throw lastError;
    }
  }

  throw lastError ?? new Error("AI planning request failed.");
}

export function planToLoopConfig(plan: AiTransferPlan, env: NodeJS.ProcessEnv = process.env): SendMtvLoopConfig {
  return buildLoopConfig({
    recipientAddress: plan.recipientAddress,
    amountMtv: plan.amountMtv,
    tokenType: plan.tokenType,
    gasMode: plan.gasMode,
    cooldownMs: plan.cooldownMs,
    maxRetries: plan.maxRetries,
    retryDelayMs: plan.retryDelayMs,
    stopEnvKey: env.STOP_ENV_KEY ?? "STOP_LOOP",
    auditLogPath: env.AUDIT_LOG_PATH,
  });
}
