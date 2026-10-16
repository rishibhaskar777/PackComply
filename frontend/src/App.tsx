import { ReactNode, useEffect, useRef, useState } from "react";
import { AnimatePresence, LazyMotion, m, MotionConfig } from "motion/react";
import { api, getJson, isExpired, isUnavailable, ApiError } from "./api/client";
import { AppShell, routeTitles } from "./app/AppShell";
import { navigate, Route, useHashRoute } from "./app/router";
import { ToastProvider } from "./components/ui";
import { useI18n } from "./i18n";
import { DURATION, EASE, useReducedMotion } from "./lib/motion";
import { useSpotlight } from "./lib/spotlight";
import { AuthPage, Registration } from "./pages/AuthPage";
import { InspectionPage } from "./pages/InspectionPage";
import { InspectionsPage } from "./pages/InspectionsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PatternsPage } from "./pages/PatternsPage";
import { ReportsPage } from "./pages/ReportsPage";
import { RiskQueuePage } from "./pages/RiskQueuePage";
import { RulesetsPage } from "./pages/RulesetsPage";
import { ThemeProvider } from "./theme/theme";
import { RiskPageData, User } from "./types";

export { ComplianceResults, FieldValue, labelFields } from "./pages/InspectionPage";

// Animation and layout features are fetched after first paint; elements render statically until then.
const loadMotionFeatures = () => import("./lib/motionFeatures").then(module => module.default);

/** Theme, motion and notification context shared by every screen. */
function AppProviders({ children }: { children: ReactNode }) {
  const reduced = useReducedMotion();
  useSpotlight();
  return (
    <ThemeProvider>
      <LazyMotion features={loadMotionFeatures} strict>
        <MotionConfig reducedMotion="user" transition={reduced ? { duration: 0 } : { duration: DURATION.base, ease: EASE }}>
          <ToastProvider>{children}</ToastProvider>
        </MotionConfig>
      </LazyMotion>
    </ThemeProvider>
  );
}

export default function App() {
  return <AppProviders><AppContent /></AppProviders>;
}

function AppContent() {
  const { t } = useI18n();
  const [user, setUser] = useState<User | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState("");
  const authRequest = useRef(0);

  async function loadUser() {
    const request = ++authRequest.current;
    try {
      const authenticatedUser = await getJson<User>("/auth/me", "authentication");
      if (request === authRequest.current) { setUser(authenticatedUser); setAuthError(""); }
    } catch (e) {
      if (request === authRequest.current) {
        setUser(null);
        if (isExpired(e)) setAuthError(t("sessionExpired"));
        else if (isUnavailable(e)) setAuthError(t("serverUnavailable"));
      }
    } finally {
      if (request === authRequest.current) setAuthLoading(false);
    }
  }
  useEffect(() => { loadUser(); }, []);

  async function login(username: string, password: string, remember: boolean) {
    setAuthBusy(true); setAuthError("");
    try {
      await api("/auth/login", "login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password, remember }) });
      await loadUser();
    } catch (e) {
      const status = (e as ApiError).status;
      setAuthError(status === 401 || status === 422 ? t("invalidCredentials") : status === 429 ? t("tooManyAttempts") : isUnavailable(e) ? t("serverUnavailable") : t("authenticationError"));
    } finally { setAuthBusy(false); }
  }
  async function register(data: Registration) {
    setAuthBusy(true); setAuthError("");
    try {
      await api("/auth/register", "register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: data.username, email: data.email, password: data.password, role: "MANUFACTURER" }) });
      await loadUser();
    } catch (e) {
      const status = (e as ApiError).status;
      setAuthError(status === 409 ? t("invalidRegistration") : status === 429 ? t("tooManyAttempts") : isUnavailable(e) ? t("serverUnavailable") : t("registrationError"));
    } finally { setAuthBusy(false); }
  }
  async function logout() {
    await api("/auth/logout", "logout", { method: "POST" }).catch(() => undefined);
    setUser(null);
    navigate("/overview", { replace: true });
  }
  function expired() { setUser(null); setAuthError(t("sessionExpired")); }

  if (authLoading) return <div className="boot-screen" role="status">{t("authenticationLoading")}</div>;
  if (!user) return <AuthPage loading={authBusy} error={authError} onLogin={login} onRegister={register} />;
  return <Workspace user={user} onLogout={logout} onExpired={expired} />;
}

function Workspace({ user, onLogout, onExpired }: { user: User; onLogout: () => void; onExpired: () => void }) {
  const { t } = useI18n();
  const reduced = useReducedMotion();
  const route = useHashRoute();
  const official = user.role === "OFFICIAL";
  const [search, setSearch] = useState("");
  // Bumped by "New inspection" so the inspection page always starts clean.
  const [newKey, setNewKey] = useState(0);
  const [riskCount, setRiskCount] = useState<number | null>(null);
  const firstRoute = useRef(true);

  async function loadRiskCount() {
    if (!official) return;
    try {
      const data = await getJson<RiskPageData>("/risk-targeting?page=1&page_size=1", "risk count");
      setRiskCount(data.summary.CRITICAL + data.summary.HIGH);
    } catch { setRiskCount(null); }
  }
  useEffect(() => { loadRiskCount(); }, [official]);
  // The risk queue is an official view; manufacturers land on the overview instead.
  useEffect(() => { if (route.name === "risk" && !official) navigate("/overview", { replace: true }); }, [route, official]);
  useEffect(() => {
    const titles = routeTitles(route);
    document.title = `${t(titles.page)} · ${t("appName")}`;
    // Move focus to the new page for keyboard and screen-reader users (not on first load).
    if (firstRoute.current) firstRoute.current = false;
    else document.getElementById("main-content")?.focus({ preventScroll: true });
  }, [route, t]);

  function newInspection() { setNewKey(key => key + 1); navigate("/new"); }

  // New and saved inspections share one page instance, so creating one does not remount the workflow.
  const pageKey = route.name === "new" || route.name === "inspection" ? `inspection-${newKey}` : route.name;
  return (
    <AppShell route={route} user={user} riskCount={riskCount} search={search} onSearchChange={setSearch} onNewInspection={newInspection} onLogout={onLogout}>
      {reduced
        ? <div key={pageKey}>{renderPage(route)}</div>
        : <AnimatePresence mode="wait" initial={false}>
          <m.div key={pageKey} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0, transition: { duration: DURATION.slow, ease: EASE } }}
            exit={{ opacity: 0, transition: { duration: DURATION.fast, ease: EASE } }}>
            {renderPage(route)}
          </m.div>
        </AnimatePresence>}
    </AppShell>
  );

  function renderPage(current: Route) {
    switch (current.name) {
      case "overview": return <OverviewPage official={official} onExpired={onExpired} showLive />;
      case "inspections": return <InspectionsPage search={search} onSearchChange={setSearch} onExpired={onExpired} />;
      case "new": case "inspection":
        return <InspectionPage inspectionId={current.name === "inspection" ? current.id : undefined} onExpired={onExpired} onCompleted={loadRiskCount} />;
      case "risk": return official ? <RiskQueuePage onExpired={onExpired} /> : null;
      case "patterns": return <PatternsPage onExpired={onExpired} />;
      case "rulesets": return <RulesetsPage onExpired={onExpired} />;
      case "reports": return <ReportsPage onExpired={onExpired} />;
    }
  }
}
