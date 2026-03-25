import { useState, useRef } from "react";
import { streamAssessment } from "./agentcore";
import "./App.css";

function App() {
  const [prompt, setPrompt] = useState("");
  const [output, setOutput] = useState("");
  const [loading, setLoading] = useState(false);
  const abortRef = useRef(false);

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
        setOutput((prev) => prev + event.data + "\n");
      }
    } catch (err) {
      setOutput((prev) => prev + `\nConnection error: ${err}`);
    } finally {
      setLoading(false);
    }
  };

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
