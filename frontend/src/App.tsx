import { lazy, ReactNode, Suspense, useEffect, useRef, useState } from "react";
import { AnimatePresence, LazyMotion, m, MotionConfig } from "motion/react";
import { api, getJson, isExpired, isUnavailable, ApiError, UNAUTHORIZED_EVENT } from "./api/client";
import { AppShell, documentTitle } from "./app/AppShell";
import { homePath, navigate, redirectFor, Route, useHashRoute } from "./app/router";
import { PageSkeleton } from "./components/PageSkeleton";
import { ToastProvider, useToast } from "./components/ui";
import { useI18n } from "./i18n";
import { DURATION, EASE, useReducedMotion } from "./lib/motion";
import { usePortalAttribute } from "./lib/portal";
import { useSpotlight } from "./lib/spotlight";
import { AuthPage, Registration } from "./pages/AuthPage";
import { ThemeProvider } from "./theme/theme";
import { FixesResponse, Panel, RiskPageData, User } from "./types";

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
const ProductsPage = lazy(() => import("./pages/maker/ProductsPage").then(module => ({ default: module.ProductsPage })));
const ProductResultPage = lazy(() => import("./pages/maker/ProductResultPage").then(module => ({ default: module.ProductResultPage })));
const FixListPage = lazy(() => import("./pages/maker/FixListPage").then(module => ({ default: module.FixListPage })));
const SelfCheckReportsPage = lazy(() => import("./pages/maker/SelfCheckReportsPage").then(module => ({ default: module.SelfCheckReportsPage })));
const RulesGuidePage = lazy(() => import("./pages/maker/RulesGuidePage").then(module => ({ default: module.RulesGuidePage })));
const NotFoundPage = lazy(() => import("./pages/NotFoundPage").then(module => ({ default: module.NotFoundPage })));
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
  const hadSession = useRef(false);
  // The portal theme follows the signed-in role and is cleared on sign-out.
  usePortalAttribute(user?.role, user !== null);

  async function loadUser() {
    const request = ++authRequest.current;
    try {
      const authenticatedUser = await getJson<User>("/auth/me", "authentication");
      if (request === authRequest.current) { hadSession.current = true; setUser(authenticatedUser); setAuthError(""); }
    } catch (e) {
      if (request === authRequest.current) {
        setUser(null);
        // A 401 is an expiry only if this tab had a session; a fresh visit is simply signed out.
        if (isExpired(e)) setAuthError(hadSession.current ? t("sessionExpired") : "");
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
    hadSession.current = false;
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

  useEffect(() => { if (!user) document.title = `${t("signIn")} · ${t("appName")}`; }, [user, t]);

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
  const [fixCount, setFixCount] = useState<number | null>(null);
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
  // The number on the Fix list nav item: open fixes across the manufacturer's latest self-checks.
  async function loadFixCount() {
    if (official) return;
    try { setFixCount((await getJson<FixesResponse>("/fixes", "fix count")).total_items); }
    catch { setFixCount(null); }
  }
  useEffect(() => { void loadFixCount(); }, [official]); // eslint-disable-line react-hooks/exhaustive-deps
  // Each role has its own pages: an inspector page opened by a manufacturer (or the reverse) is replaced by its equivalent.
  const redirect = redirectFor(route, user.role);
  useEffect(() => { if (redirect) navigate(redirect, { replace: true }); }, [redirect]);
  useEffect(() => { document.title = documentTitle(route, user.role, t, pageLabel); }, [route, t, pageLabel, user.role]);
  useEffect(() => {
    // Move focus to the new page for keyboard and screen-reader users (not on first load).
    if (firstRoute.current) firstRoute.current = false;
    else document.getElementById("main-content")?.focus({ preventScroll: true });
  }, [route, t]);

  // Phones go to the field app; larger screens keep the upload form.
  function newInspection() {
    setNewKey(key => key + 1);
    const phone = typeof window.matchMedia === "function" && window.matchMedia("(max-width: 767px)").matches;
    navigate(phone ? "/capture" : official ? "/new" : "/check");
  }
  const onCompleted = () => { void loadRiskCount(); void loadFixCount(); };

  if (route.name === "capture") {
    const add = route.add ? { inspectionId: route.add, panel: (route.panel as Panel | undefined) ?? "BACK" } : undefined;
    return (
      <Suspense fallback={<div className="boot-screen" role="status">{t("loading")}</div>}>
        <CapturePage key={`capture-${newKey}-${route.add ?? ""}-${route.panel ?? ""}`} official={official} onExpired={onExpired} onCompleted={onCompleted} add={add} />
      </Suspense>
    );
  }
  const pageKey = route.name === "new" || route.name === "check" ? `${route.name}-${newKey}-${route.name === "check" ? route.previous ?? "" : ""}` : route.name === "inspection" || route.name === "product" ? `${route.name}-${route.id}` : route.name;
  return (
    <AppShell route={route} pageLabel={route.name === "inspection" || route.name === "product" ? pageLabel : null} user={user} riskCount={riskCount} fixCount={fixCount} search={search} onSearchChange={setSearch} onNewInspection={newInspection} onLogout={onLogout} onExpired={onExpired}>
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
    // A page for the other role is never rendered (nor its data requested) while the redirect happens.
    if (redirectFor(current, user.role)) return null;
    switch (current.name) {
      case "overview": return <OverviewPage official={official} onExpired={onExpired} showLive />;
      case "inspections": return <InspectionsPage search={search} onSearchChange={setSearch} onExpired={onExpired} />;
      case "new": return <NewInspectionPage onExpired={onExpired} onCompleted={onCompleted} />;
      case "check": return <NewInspectionPage maker previousId={current.previous} onExpired={onExpired} onCompleted={onCompleted} />;
      case "products": return <ProductsPage onExpired={onExpired} />;
      case "product": return <ProductResultPage key={current.id} inspectionId={current.id} onExpired={onExpired} onTitle={setPageLabel} />;
      case "fixes": return <FixListPage onExpired={onExpired} />;
      case "guide": return <RulesGuidePage onExpired={onExpired} />;
      case "inspection": return <InspectionRecordPage key={current.id} inspectionId={current.id} official={official} inspectorName={user.full_name || user.username} onExpired={onExpired} onChanged={onCompleted} onTitle={setPageLabel} />;
      case "capture": return null;
      case "risk": return <RiskQueuePage onExpired={onExpired} />;
      case "patterns": return <PatternsPage onExpired={onExpired} />;
      case "rulesets": return <RulesetsPage onExpired={onExpired} />;
      case "reports": return official ? <ReportsPage onExpired={onExpired} /> : <SelfCheckReportsPage onExpired={onExpired} />;
      case "about": return <AboutPage onExpired={onExpired} />;
      case "notfound": return <NotFoundPage official={official} />;
    }
  }
}
