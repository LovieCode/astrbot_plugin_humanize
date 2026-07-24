import { spawn } from "node:child_process";

import { Type } from "@earendil-works/pi-ai";
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";

const MAX_OUTPUT_CHARS = 24_000;
const MAX_MEMORY_CHARS = 4_000;

function truncate(text: string): string {
  if (text.length <= MAX_OUTPUT_CHARS) return text;
  return `${text.slice(0, MAX_OUTPUT_CHARS)}\n\n[OpenViking output truncated]`;
}

async function runOpenViking(args: string[], signal: AbortSignal): Promise<string> {
  return await new Promise((resolve, reject) => {
    const child = spawn("openviking", [...args, "--output", "json"], {
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";

    const stop = () => child.kill();
    signal.addEventListener("abort", stop, { once: true });
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => (stdout += chunk));
    child.stderr.on("data", (chunk: string) => (stderr += chunk));
    child.on("error", (error) => {
      signal.removeEventListener("abort", stop);
      reject(error);
    });
    child.on("close", (code) => {
      signal.removeEventListener("abort", stop);
      if (signal.aborted) {
        reject(new Error("OpenViking command was cancelled."));
      } else if (code !== 0) {
        reject(new Error(truncate(stderr || stdout || `OpenViking exited with code ${code}.`)));
      } else {
        resolve(truncate(stdout));
      }
    });
  });
}

const findTool = defineTool({
  name: "openviking_find",
  label: "OpenViking Find",
  description: "Search relevant durable context and memories in OpenViking.",
  promptSnippet: "Search durable context and memories in OpenViking",
  promptGuidelines: [
    "Use openviking_find before answering a request that may depend on prior user preferences, decisions, or project history.",
  ],
  parameters: Type.Object({
    query: Type.String({ minLength: 1, maxLength: 2_000, description: "Focused semantic search query" }),
    uri: Type.Optional(Type.String({ minLength: 10, maxLength: 2_048, description: "Optional viking:// scope" })),
    limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 10, description: "Maximum results" })),
    contextType: Type.Optional(
      Type.String({ minLength: 1, maxLength: 32, description: "Optional type: memory, resource, or skill" }),
    ),
  }),
  async execute(_toolCallId, params, signal) {
    const args = ["find", params.query];
    if (params.uri) args.push("--uri", params.uri);
    if (params.limit) args.push("--limit", String(params.limit));
    if (params.contextType) {
      if (!new Set(["memory", "resource", "skill"]).has(params.contextType)) {
        throw new Error("contextType must be memory, resource, or skill.");
      }
      args.push("--context-type", params.contextType);
    }
    const result = await runOpenViking(args, signal);
    return { content: [{ type: "text", text: result }], details: { operation: "find" } };
  },
});

const readTool = defineTool({
  name: "openviking_read",
  label: "OpenViking Read",
  description: "Read the exact content of a viking:// resource returned by OpenViking search.",
  promptSnippet: "Read an exact OpenViking resource by viking:// URI",
  promptGuidelines: ["Use openviking_read only for a viking:// URI returned by openviking_find or supplied by the user."],
  parameters: Type.Object({
    uri: Type.String({ minLength: 10, maxLength: 2_048, pattern: "^viking://", description: "Resource URI" }),
  }),
  async execute(_toolCallId, params, signal) {
    const result = await runOpenViking(["read", params.uri], signal);
    return { content: [{ type: "text", text: result }], details: { operation: "read" } };
  },
});

const rememberTool = defineTool({
  name: "openviking_remember",
  label: "OpenViking Remember",
  description: "Persist a user-approved durable memory in OpenViking.",
  promptSnippet: "Persist an explicitly user-approved durable memory in OpenViking",
  promptGuidelines: [
    "Use openviking_remember only when the user explicitly asks to remember or save information; never store secrets, credentials, or unverified content.",
  ],
  parameters: Type.Object({
    content: Type.String({ minLength: 1, maxLength: MAX_MEMORY_CHARS, description: "User-approved memory to store" }),
  }),
  async execute(_toolCallId, params, signal) {
    const result = await runOpenViking(["add-memory", params.content], signal);
    return { content: [{ type: "text", text: result }], details: { operation: "remember" } };
  },
});

const healthTool = defineTool({
  name: "openviking_health",
  label: "OpenViking Health",
  description: "Check OpenViking service connectivity and health.",
  promptSnippet: "Check OpenViking service health",
  parameters: Type.Object({}),
  async execute(_toolCallId, _params, signal) {
    const result = await runOpenViking(["health"], signal);
    return { content: [{ type: "text", text: result }], details: { operation: "health" } };
  },
});

export default function openvikingExtension(pi: ExtensionAPI) {
  pi.registerTool(findTool);
  pi.registerTool(readTool);
  pi.registerTool(rememberTool);
  pi.registerTool(healthTool);
}
