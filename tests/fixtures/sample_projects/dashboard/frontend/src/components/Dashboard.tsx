import { useState } from "react";

interface PanelProps {
  title: string;
  subtitle: string;
  loading: boolean;
  error: string | null;
  collapsed: boolean;
  onToggle: () => void;
  onRefresh: () => void;
  badge: string;
}

export function Dashboard({ items }: { items: string[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="dashboard">
      <header>
        <nav>
          <ul>
            <li>
              <a onClick={() => setOpen(!open)}>
                <span>
                  <strong>Pulse</strong>
                </span>
              </a>
            </li>
          </ul>
        </nav>
      </header>
      <ul className="items">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
      <section className="rows">
        <div className="row">
          <span>name</span>
          <span>value</span>
        </div>
        <div className="row">
          <span>name</span>
          <span>value</span>
        </div>
        <div className="row">
          <span>name</span>
          <span>value</span>
        </div>
      </section>
      {open ? <Panel title="x" subtitle="y" loading={false} error={null} collapsed={false} onToggle={() => {}} onRefresh={() => {}} badge="3" /> : null}
      <Sidebar />
    </div>
  );
}

function Panel(props: PanelProps) {
  return <div className="panel">{props.title}</div>;
}

function Sidebar() {
  return <aside className="sidebar">nav</aside>;
}
