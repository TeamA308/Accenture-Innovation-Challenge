import React, { useCallback, useEffect, useState } from "react";
import { api } from "./lib/api";
import { Dashboard } from "./pages/Dashboard";
import { Evidence } from "./pages/Evidence";
import { ReviewQueue } from "./pages/ReviewQueue";
import { Policies } from "./pages/Policies";
import { Trust } from "./pages/Trust";
import { BiasMirror } from "./pages/BiasMirror";

type View = "dashboard" | "review" | "bias" | "policy" | "trust" | "evidence";

const NAV: { id: View; label: string; icon: string }[] = [
  { id: "dashboard", label: "Live oversight", icon: "◉" },
  { id: "review", label: "Review queue", icon: "☰" },
  { id: "bias", label: "Bias mirror", icon: "⇄" },
  { id: "policy", label: "Policy console", icon: "⚙" },
  { id: "trust", label: "Trust report", icon: "◐" },
];

export default function App() {
  const [view, setView] = useState<View>("dashboard");
  const [evidenceId, setEvidenceId] = useState<string | null>(null);
  const [queueCount, setQueueCount] = useState(0);
  const [health, setHealth] = useState<any>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ status: "unreachable" }));
    const t = setInterval(() => {
      api.reviewStats().then((s) => setQueueCount(s.pending)).catch(() => {});
    }, 5000);
    api.reviewStats().then((s) => setQueueCount(s.pending)).catch(() => {});
    return () => clearInterval(t);
  }, []);

  const openEvidence = useCallback((id: string) => {
    setEvidenceId(id);
    setView("evidence");
  }, []);

  const onCounts = useCallback((c: { queue: number }) => setQueueCount(c.queue), []);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">C</div>
          <div>
            <div className="brand-name">ControlPlane.ai</div>
            <div className="brand-sub">Team A308</div>
          </div>
        </div>

        {NAV.map((n) => (
          <button
            key={n.id}
            className={`nav-item${view === n.id ? " active" : ""}`}
            onClick={() => setView(n.id)}
          >
            <span>{n.icon}</span>
            {n.label}
            {n.id === "review" && queueCount > 0 && <span className="count">{queueCount}</span>}
          </button>
        ))}

        {view === "evidence" && (
          <button className="nav-item active">
            <span>◇</span> Evidence record
          </button>
        )}

        <div className="sidebar-foot">
          <div style={{ marginBottom: 6 }}>
            {health?.status === "ok" ? (
              <>
                <span className="conn live"><i /> backend up</span>
                <div style={{ marginTop: 4 }}>
                  model: <code>{health.provider}/{health.model}</code>
                </div>
                <div>personal-data engine: <code>{health.pii_engine}</code></div>
              </>
            ) : (
              <span className="conn"><i /> backend unreachable</span>
            )}
          </div>
          <div style={{ opacity: 0.7 }}>
            Ring 0 inline · Ring 1 deep · Ring 2 human
          </div>
        </div>
      </aside>

      <main className="main">
        {view === "dashboard" && <Dashboard onOpen={openEvidence} onCounts={onCounts} />}
        {view === "review" && <ReviewQueue onOpen={openEvidence} />}
        {view === "bias" && <BiasMirror />}
        {view === "policy" && <Policies />}
        {view === "trust" && <Trust />}
        {view === "evidence" && evidenceId && (
          <Evidence id={evidenceId} onBack={() => setView("dashboard")} />
        )}
      </main>
    </div>
  );
}
