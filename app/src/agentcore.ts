const RUNTIME_URL = import.meta.env.VITE_AGENTCORE_URL || "http://localhost:8080";

export interface AssessmentEvent {
  type: "chunk" | "complete" | "error";
  data: string;
}

export async function* streamAssessment(
  prompt: string
): AsyncGenerator<AssessmentEvent> {
  const runInput = {
    thread_id: crypto.randomUUID(),
    run_id: crypto.randomUUID(),
    messages: [{ role: "user", content: prompt }],
    tools: [],
    context: [],
    forwarded_props: {},
  };

  const response = await fetch(`${RUNTIME_URL}/invocations`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(runInput),
  });

  if (!response.ok) {
    yield { type: "error", data: `Agent returned ${response.status}` };
    return;
  }

  const reader = response.body?.getReader();
  if (!reader) {
    yield { type: "error", data: "No response stream" };
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || !trimmed.startsWith("data: ")) continue;

      try {
        const event = JSON.parse(trimmed.slice(6));
        if (event.type === "TEXT_MESSAGE_CONTENT") {
          yield { type: "chunk", data: event.delta };
        } else if (event.type === "RUN_FINISHED") {
          yield { type: "complete", data: "" };
        } else if (event.type === "RUN_ERROR") {
          yield { type: "error", data: event.message || "Agent error" };
        }
      } catch {
        // Skip malformed SSE lines
      }
    }
  }
}
