import { Component, ErrorInfo, ReactNode } from "react";
import { TriangleAlert } from "lucide-react";
import { buttonClass } from "../components/ui/Button";
import { useI18n } from "../i18n";

/** The last line of defence: a render error or a failed code chunk shows this instead of a blank page. */
function Fallback() {
  const { t } = useI18n();
  return (
    <div className="boot-screen error-screen" role="alert">
      <div className="error-screen__card">
        <TriangleAlert size={28} aria-hidden="true" />
        <h1>{t("errorTitle")}</h1>
        <p>{t("errorBody")}</p>
        <button type="button" className={buttonClass()} onClick={() => window.location.reload()}>{t("errorReload")}</button>
      </div>
    </div>
  );
}

type State = { failed: boolean };

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State { return { failed: true }; }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // The details stay in the console for whoever is running the demo; nothing is sent anywhere.
    console.error("PackComply UI error", error, info.componentStack);
  }

  render() { return this.state.failed ? <Fallback /> : this.props.children; }
}
