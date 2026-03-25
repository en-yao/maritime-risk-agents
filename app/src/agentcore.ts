const RUNTIME_URL = import.meta.env.VITE_AGENTCORE_URL || "http://localhost:8080";

export interface AssessmentEvent {
  type: "chunk" | "complete" | "error";
  data: string;
}

export async function* streamAssessment(
  prompt: string
): AsyncGenerator<AssessmentEvent> {
  const response = await fetch(`${RUNTIME_URL}/invocations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
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
      if (line.trim()) {
        yield { type: "chunk", data: line };
      }
    }
  }

  if (buffer.trim()) {
    yield { type: "complete", data: buffer };
  }
}
