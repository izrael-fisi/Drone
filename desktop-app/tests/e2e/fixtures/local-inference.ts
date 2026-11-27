import { once } from "node:events";
import { createServer } from "node:http";

export async function localInferenceFixture(delay = 0) {
  const requests: { frames: string[]; text: string[]; startedAt: number; endedAt?: number; schema: unknown }[] = [];
  const server = createServer(async (request, response) => {
    if (request.url !== "/v1/chat/completions" || request.method !== "POST") {
      response.writeHead(404).end();
      return;
    }
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    const body = JSON.parse(Buffer.concat(chunks).toString());
    const parts = body.messages[1].content as { type: string; text?: string; image_url?: { url: string } }[];
    const frames = parts.flatMap((part) => part.image_url ? [part.image_url.url] : []);
    const text = parts.flatMap((part) => part.text ? [part.text] : []);
    const entry: typeof requests[number] = { frames, text, startedAt: Date.now(), schema: body.response_format };
    requests.push(entry);
    if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
    const eventFrame = frames.length ? text.slice(1).map((value) => JSON.parse(value)).find((row) => row.eventFrame) : undefined;
    const automatic = body.response_format.json_schema.schema.properties.keyEvents;
    const references = automatic ? (JSON.parse(text[0]) as { ref: string }[]).map(({ ref }) => ref) : [];
    const result = frames.length ? { outcome: "observed", explanation: "Fixture review at the event timestamp.", evidenceRefs: [eventFrame.ref] } :
      automatic ? { keyEvents: references.slice(0, 6) } :
      { summary: [{ text: "The gateway recorded the incident.", evidenceRefs: ["incident"] }],
        nextAction: { text: "Review the gate recording.", evidenceRefs: ["incident"] } };
    entry.endedAt = Date.now();
    response.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify({
      choices: [{ finish_reason: "stop", message: { content: JSON.stringify(result) } }],
      usage: { prompt_tokens: 80, completion_tokens: 21 },
    }));
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const origin = "http://127.0.0.1:" + (server.address() as { port: number }).port;
  return { origin, requests, close: () => new Promise<void>((resolve) => {
    server.close(() => resolve());
    server.closeAllConnections();
  }) };
}
