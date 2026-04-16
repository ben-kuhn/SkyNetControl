import { useEffect, useState } from "react";

interface HealthStatus {
  status: string;
  version: string;
  database: string;
}

function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((res) => res.json())
      .then(setHealth)
      .catch((err) => setError(err.message));
  }, []);

  return (
    <div className="app">
      <h1>SkyNetControl</h1>
      <p>Winlink Net Management</p>
      {error && <p className="error">API Error: {error}</p>}
      {health && (
        <div className="status">
          <p>Status: {health.status}</p>
          <p>Version: {health.version}</p>
          <p>Database: {health.database}</p>
        </div>
      )}
    </div>
  );
}

export default App;
