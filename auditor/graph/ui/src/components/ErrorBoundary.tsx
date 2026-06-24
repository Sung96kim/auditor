import { Component, type ErrorInfo, type ReactNode } from "react";
import { THEME } from "../theme";

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

/** Catches render errors so an unexpected failure shows a recoverable message instead of a
 * blank page. (Effect-level errors are handled at their source.) */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("graph UI error:", error, info.componentStack);
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <div
        style={{
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: "14px",
          background: THEME.bgApp,
          color: "#c8d3e0",
          fontFamily: "ui-sans-serif, system-ui, sans-serif",
        }}
      >
        <div style={{ fontSize: "14px" }}>Something went wrong rendering the graph.</div>
        <div style={{ fontSize: "12px", color: "#64748b", maxWidth: 480 }}>
          {String(this.state.error?.message ?? this.state.error)}
        </div>
        <button
          onClick={() => this.setState({ error: null })}
          style={{
            background: THEME.accent,
            border: "none",
            borderRadius: "8px",
            color: "#fff",
            padding: "8px 16px",
            cursor: "pointer",
            fontSize: "13px",
          }}
        >
          Try again
        </button>
      </div>
    );
  }
}
