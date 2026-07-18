import { useEffect } from "react";

function App() {
  useEffect(() => {
    window.location.replace("/preview.html");
  }, []);
  return (
    <div style={{
      minHeight: "100vh",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      fontFamily: "Inter, system-ui, sans-serif",
      background: "#F1F8FE",
      color: "#0B1E33",
    }}>
      Loading EchoWave preview…
    </div>
  );
}

export default App;
