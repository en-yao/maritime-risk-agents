import {
  CognitoUserPool,
  CognitoUser,
  AuthenticationDetails,
  CognitoUserSession,
} from "amazon-cognito-identity-js";

const AGENTCORE_REGION = import.meta.env.VITE_AGENTCORE_REGION || "ap-southeast-1";
const AGENT_ARN = import.meta.env.VITE_AGENT_ARN || "";
const COGNITO_POOL_ID = import.meta.env.VITE_COGNITO_USER_POOL_ID || "";
const COGNITO_CLIENT_ID = import.meta.env.VITE_COGNITO_CLIENT_ID || "";

// For local dev, fall back to direct HTTP without auth
const LOCAL_URL = "http://localhost:8080";

const userPool = COGNITO_POOL_ID
  ? new CognitoUserPool({ UserPoolId: COGNITO_POOL_ID, ClientId: COGNITO_CLIENT_ID })
  : null;

function getInvocationUrl(): string {
  if (!AGENT_ARN) return `${LOCAL_URL}/invocations`;
  const escaped = encodeURIComponent(AGENT_ARN);
  return `https://bedrock-agentcore.${AGENTCORE_REGION}.amazonaws.com/runtimes/${escaped}/invocations?qualifier=DEFAULT`;
}

async function getAccessToken(): Promise<string> {
  if (!userPool) return "";

  return new Promise((resolve, reject) => {
    const currentUser = userPool.getCurrentUser();
    if (!currentUser) {
      reject(new Error("Not signed in"));
      return;
    }
    currentUser.getSession((err: Error | null, session: CognitoUserSession | null) => {
      if (err || !session) {
        reject(err || new Error("No session"));
        return;
      }
      resolve(session.getAccessToken().getJwtToken());
    });
  });
}

export async function signIn(email: string, password: string): Promise<void> {
  if (!userPool) throw new Error("Cognito not configured");

  return new Promise((resolve, reject) => {
    const user = new CognitoUser({ Username: email, Pool: userPool });
    user.authenticateUser(new AuthenticationDetails({ Username: email, Password: password }), {
      onSuccess: () => resolve(),
      onFailure: (err) => reject(err),
      newPasswordRequired: () => reject(new Error("Password change required")),
    });
  });
}

export async function signUp(email: string, password: string): Promise<void> {
  if (!userPool) throw new Error("Cognito not configured");

  return new Promise((resolve, reject) => {
    userPool.signUp(email, password, [], [], (err) => {
      if (err) reject(err);
      else resolve();
    });
  });
}

export function isSignedIn(): boolean {
  if (!userPool) return true; // No auth needed for local dev
  const user = userPool.getCurrentUser();
  return user !== null;
}

export interface AssessmentEvent {
  type: "chunk" | "complete" | "error";
  data: string;
}

export async function* streamAssessment(
  prompt: string
): AsyncGenerator<AssessmentEvent> {
  const url = getInvocationUrl();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };

  // Add auth token for deployed AgentCore
  if (AGENT_ARN && userPool) {
    try {
      const token = await getAccessToken();
      headers["Authorization"] = `Bearer ${token}`;
    } catch {
      yield { type: "error", data: "Not signed in. Please sign in first." };
      return;
    }
  }

  const sessionId = crypto.randomUUID() + "-" + crypto.randomUUID().slice(0, 8);
  headers["X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"] = sessionId;

  const runInput = {
    thread_id: crypto.randomUUID(),
    run_id: crypto.randomUUID(),
    messages: [{ role: "user", content: prompt }],
    tools: [],
    context: [],
    forwarded_props: {},
  };

  const response = await fetch(url, {
    method: "POST",
    headers,
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
