import React, { Component, ErrorInfo, ReactNode } from "react";
import { AlertCircle, RefreshCw } from "lucide-react";

interface Props {
  children: ReactNode;
  fallbackTitle?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("ErrorBoundary caught an error:", error, errorInfo);
  }

  private handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  public render() {
    if (this.state.hasError) {
      return (
        <div
          className="card-panel"
          style={{
            margin: "24px",
            padding: "32px",
            textAlign: "center",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: "16px",
            border: "1px solid var(--loss-color)",
          }}
        >
          <AlertCircle size={40} color="var(--loss-color)" />
          <div style={{ fontSize: "16px", fontWeight: 700, color: "var(--text-heading)" }}>
            {this.props.fallbackTitle || "页面渲染异常"}
          </div>
          <div
            style={{
              fontSize: "12px",
              color: "var(--loss-color)",
              fontFamily: "monospace",
              maxWidth: "600px",
              background: "var(--table-alternate)",
              padding: "12px",
              borderRadius: "60px",
              wordBreak: "break-all",
            }}
          >
            {this.state.error?.message || "未知组件错误"}
          </div>
          <button onClick={this.handleReset} className="btn btn-primary" style={{ display: "flex", gap: "6px" }}>
            <RefreshCw size={14} />
            <span>重试加载</span>
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
