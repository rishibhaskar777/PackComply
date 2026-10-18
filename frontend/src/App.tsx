import { lazy, ReactNode, Suspense, useEffect, useRef, useState } from "react";
import { AnimatePresence, LazyMotion, m, MotionConfig } from "motion/react";
import { api, getJson, isExpired, isUnavailable, ApiError, UNAUTHORIZED_EVENT } from "./api/client";
import { AppShell, routeTitles } from "./app/AppShell";
import { navigate, Route, useHashRoute } from "./app/router";
import { PageSkeleton } from "./components/PageSkeleton";
import { ToastProvider, useToast } from "./components/ui";
import { useI18n } from "./i18n";
import { DURATION, EASE, useReducedMotion } from "./lib/motion";
import { useSpotlight } from "./lib/spotlight";
import { AuthPage, Registration } from "./pages/AuthPage";
import { ThemeProvider } from "./theme/theme";
import { Panel, RiskPageData, User } from "./types";

export { ComplianceResults, FieldValue, labelFields } from "./pages/InspectionPage";

// Every page is its own chunk, fetched when its route is first opened. The field app is only opened on phones.
const CapturePage = lazy(() => import("./pages/capture/CapturePage").then(module => ({ default: module.CapturePage })));
const OverviewPage = lazy(() => import("./pages/OverviewPage").then(module => ({ default: module.OverviewPage })));
const InspectionsPage = lazy(() => import("./pages/InspectionsPage").then(module => ({ default: module.InspectionsPage })));
const NewInspectionPage = lazy(() => import("./pages/NewInspectionPage").then(module => ({ default: module.NewInspectionPage })));
const InspectionRecordPage = lazy(() => import("./pages/InspectionRecordPage").then(module => ({ default: module.InspectionRecordPage })));
const RiskQueuePage = lazy(() => import("./pages/RiskQueuePage").then(module => ({ default: module.RiskQueuePage })));
const PatternsPage = lazy(() => import("./pages/PatternsPage").then(module => ({ default: module.PatternsPage })));
const RulesetsPage = lazy(() => import("./pages/RulesetsPage").then(module => ({ default: module.RulesetsPage })));
const AboutPage = lazy(() => import("./pages/AboutPage").then(module => ({ default: module.AboutPage })));
const ReportsPage = lazy(() => import("./pages/ReportsPage").then(module => ({ default: module.ReportsPage })));

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

  // Any request answered 401 (the session ended while the app was open) returns to sign-in with a notice, once.
  const toast = useToast();
  const signedIn = useRef(false);
  signedIn.current = user !== null;
  useEffect(() => {
    function onUnauthorized() {
      if (!signedIn.current) return;
      signedIn.current = false;
      setUser(null); setAuthError(t("sessionExpired")); toast(t("sessionExpired"), "error");
      navigate("/overview", { replace: true });
    }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [t, toast]);

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
  // Bumped by "New inspection" so the upload form always starts clean.
  const [newKey, setNewKey] = useState(0);
  const [riskCount, setRiskCount] = useState<number | null>(null);
  const [pageLabel, setPageLabel] = useState<string | null>(null);
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

  // Phones go to the field app; larger screens keep the upload form.
  function newInspection() {
    setNewKey(key => key + 1);
    navigate(typeof window.matchMedia === "function" && window.matchMedia("(max-width: 767px)").matches ? "/capture" : "/new");
  }

  if (route.name === "capture") {
    const add = route.add ? { inspectionId: route.add, panel: (route.panel as Panel | undefined) ?? "BACK" } : undefined;
    return (
      <Suspense fallback={<div className="boot-screen" role="status">{t("loading")}</div>}>
        <CapturePage key={`capture-${newKey}-${route.add ?? ""}-${route.panel ?? ""}`} official={official} onExpired={onExpired} onCompleted={loadRiskCount} add={add} />
      </Suspense>
    );
  }
  const pageKey = route.name === "new" ? `new-${newKey}` : route.name === "inspection" ? `inspection-${route.id}` : route.name;
  return (
    <AppShell route={route} pageLabel={route.name === "inspection" ? pageLabel : null} user={user} riskCount={riskCount} search={search} onSearchChange={setSearch} onNewInspection={newInspection} onLogout={onLogout} onExpired={onExpired}>
      {reduced
        ? <div key={pageKey}><Suspense fallback={<PageSkeleton />}>{renderPage(route)}</Suspense></div>
        : <AnimatePresence mode="wait" initial={false}>
          <m.div key={pageKey} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0, transition: { duration: DURATION.slow, ease: EASE } }}
            exit={{ opacity: 0, transition: { duration: DURATION.fast, ease: EASE } }}>
            <Suspense fallback={<PageSkeleton />}>{renderPage(route)}</Suspense>
          </m.div>
        </AnimatePresence>}
    </AppShell>
  );

  function renderPage(current: Route) {
    switch (current.name) {
      case "overview": return <OverviewPage official={official} onExpired={onExpired} showLive />;
      case "inspections": return <InspectionsPage search={search} onSearchChange={setSearch} onExpired={onExpired} />;
      case "new": return <NewInspectionPage onExpired={onExpired} onCompleted={loadRiskCount} />;
      case "inspection": return <InspectionRecordPage key={current.id} inspectionId={current.id} official={official} onExpired={onExpired} onChanged={loadRiskCount} onTitle={setPageLabel} />;
      case "capture": return null;
      case "risk": return official ? <RiskQueuePage onExpired={onExpired} /> : null;
      case "patterns": return <PatternsPage onExpired={onExpired} />;
      case "rulesets": return <RulesetsPage onExpired={onExpired} />;
      case "reports": return <ReportsPage onExpired={onExpired} />;
      case "about": return <AboutPage onExpired={onExpired} />;
    }
  }
}
