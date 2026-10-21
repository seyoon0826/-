export default function Placeholder({ title, phase, children }: { title: string; phase: string; children?: React.ReactNode }) {
  return (
    <div className="panel" style={{ padding: 28 }}>
      <div className="panel-h">
        <span>{title}</span>
        <span className="chip">{phase}</span>
      </div>
      <p style={{ margin: 0, color: "var(--text-2)" }}>{children}</p>
    </div>
  );
}
