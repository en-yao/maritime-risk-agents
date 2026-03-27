import { useState, useRef } from "react";
import { streamAssessment, signIn, signUp, isSignedIn } from "./agentcore";
import "./App.css";

function App() {
  const [prompt, setPrompt] = useState("");
  const [output, setOutput] = useState("");
  const [loading, setLoading] = useState(false);
  const [authed, setAuthed] = useState(isSignedIn());
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState("");
  const [authMode, setAuthMode] = useState<"signin" | "signup">("signin");
  const abortRef = useRef(false);

  const handleAuth = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthError("");
    try {
      if (authMode === "signup") {
        await signUp(email, password);
        setAuthError("Check your email for a verification code, then sign in.");
        setAuthMode("signin");
        return;
      }
      await signIn(email, password);
      setAuthed(true);
    } catch (err) {
      setAuthError(String(err));
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim() || loading) return;

    setOutput("");
    setLoading(true);
    abortRef.current = false;

    try {
      for await (const event of streamAssessment(prompt)) {
        if (abortRef.current) break;
        if (event.type === "error") {
          setOutput((prev) => prev + `\nError: ${event.data}`);
          break;
        }
        setOutput((prev) => prev + event.data);
      }
    } catch (err) {
      setOutput((prev) => prev + `\nConnection error: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  if (!authed) {
    return (
      <div className="app">
        <header>
          <h1>Maritime Risk Assessment</h1>
          <p>Sign in to access the assessment tool</p>
        </header>
        <form onSubmit={handleAuth}>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Email"
          />
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
          />
          <button type="submit">
            {authMode === "signin" ? "Sign In" : "Sign Up"}
          </button>
          <button type="button" onClick={() => setAuthMode(authMode === "signin" ? "signup" : "signin")}>
            {authMode === "signin" ? "Need an account? Sign up" : "Have an account? Sign in"}
          </button>
        </form>
        {authError && <pre className="output">{authError}</pre>}
      </div>
    );
  }

  return (
    <div className="app">
      <header>
        <h1>Maritime Risk Assessment</h1>
        <p>Enter a shipment route to assess delay risk and reroute options</p>
      </header>

      <form onSubmit={handleSubmit}>
        <input
          type="text"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Shanghai to Rotterdam, departing 2024-08-15"
          disabled={loading}
        />
        <button type="submit" disabled={loading}>
          {loading ? "Assessing..." : "Assess Risk"}
        </button>
      </form>

      {output && (
        <pre className="output">
          {output}
        </pre>
      )}
    </div>
  );
}

export default App;
