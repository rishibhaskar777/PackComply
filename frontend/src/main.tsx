import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/geist/wght.css";
import "@fontsource-variable/geist-mono/wght.css";
import "./styles/tokens.css";
import "./components/ui/ui.css";
import "./styles/app.css";
import "./styles/record.css";
import "./styles/auth.css";
import "./styles/progress.css";
import "./styles/capture.css";
import App from "./App";
import { I18nProvider } from "./i18n";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider><App /></I18nProvider>
  </StrictMode>,
);
