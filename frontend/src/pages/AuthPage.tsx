import { FormEvent, lazy, Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { ArrowLeft, ArrowRight, Lock, Mail, ShieldCheck, User, WifiOff } from "lucide-react";
import { BrandMark } from "../app/BrandMark";
import { DEFAULT_PATH, FORGOT_PASSWORD_PATH, navigate, useHashPath } from "../app/router";
import { ThemeToggle } from "../app/ThemeControls";
import { FALLBACK_RULE_COUNT, useSystemStatus } from "../app/useSystemStatus";
import { LanguageSelector } from "../components/LanguageSelector";
import { Alert, Button, Input, Tabs } from "../components/ui";
import { fill, useI18n } from "../i18n";
import { DURATION, EASE, useReducedMotion } from "../lib/motion";
import { Portal } from "../lib/portal";
import { IconField, PasswordToggle } from "./auth/fields";
import { RuleStrip } from "./auth/LabelCard";
import { StoryPanel } from "./auth/StoryPanel";
import { StatusPill, StatusStrip } from "./auth/SystemStatus";

// The password-reset flow is only fetched when someone opens it.
const ForgotPassword = lazy(() => import("./auth/ForgotPassword").then(module => ({ default: module.ForgotPassword })));

export type Registration = { username: string; email: string; password: string; confirmPassword: string };
type AuthPageProps = { loading: boolean; error: string; onLogin: (username: string, password: string, remember: boolean, portal: Portal) => Promise<Portal | null> | void; onRegister: (data: Registration) => void };
type View = "login" | "register" | "forgot";

// Mirrors LOGIN_MAX_ATTEMPTS on the server (default 5).
const LOCKOUT_ATTEMPTS = 5;
export const PORTAL_STORAGE_KEY = "packcomply-login-portal";

/** The tab last used on this browser; storage may be blocked, so Inspector is the fallback. */
function initialPortal(): Portal {
  try {
    const saved = localStorage.getItem(PORTAL_STORAGE_KEY);
    if (saved === "inspector" || saved === "manufacturer") return saved;
  } catch { /* storage blocked */ }
  return "inspector";
}

export function AuthPage({ loading, error, onLogin, onRegister }: AuthPageProps) {
  const { t } = useI18n();
  const reduced = useReducedMotion();
  const status = useSystemStatus();
  const path = useHashPath();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [portal, setPortal] = useState<Portal>(initialPortal);
  const view: View = path === FORGOT_PASSWORD_PATH ? "forgot" : mode;
  const ruleCount = status.phase === "ready" ? status.data.rule_count : FALLBACK_RULE_COUNT;
  // Where "Back to sign in" returns after the reset flow (the page the user was trying to reach).
  const returnPath = useRef(path && path !== FORGOT_PASSWORD_PATH ? path : DEFAULT_PATH);
  useEffect(() => { if (path && path !== FORGOT_PASSWORD_PATH) returnPath.current = path; }, [path]);

  // The sign-in page takes the accent of the selected tab (registration is manufacturer-only). The tab is a hint, never a permission.
  useLayoutEffect(() => {
    const root = document.documentElement;
    root.dataset.portal = view === "register" ? "manufacturer" : portal;
    return () => { delete root.dataset.portal; };
  }, [portal, view]);
  function choosePortal(next: Portal) {
    setPortal(next);
    try { localStorage.setItem(PORTAL_STORAGE_KEY, next); } catch { /* storage blocked */ }
  }

  // After the first view, every view change moves focus to the new heading.
  const navigated = useRef(false);
  useEffect(() => { if (view === "forgot") navigated.current = true; }, [view]);
  function show(next: "login" | "register") { navigated.current = true; setMode(next); }
  function backFromForgot() { navigated.current = true; setMode("login"); navigate(returnPath.current); }

  const panel = view === "forgot"
    ? <Suspense fallback={<div className="boot-screen" role="status">{t("loading")}</div>}><ForgotPassword onBack={backFromForgot} reduced={reduced} /></Suspense>
    : <AccountForms mode={view} portal={portal} onPortalChange={choosePortal} focusHeading={navigated.current} loading={loading} error={error} onLogin={onLogin} onRegister={onRegister} onShow={show} />;

  return (
    <div className="signin">
      <StoryPanel ruleCount={ruleCount} reduced={reduced} />
      <main className="signin-main">
        <header className="signin-topbar">
          <StatusPill state={status} />
          <div className="signin-topbar__controls"><LanguageSelector compact /><ThemeToggle /></div>
        </header>
        <div className="signin-column">
          <div className="signin-mobile-brand">
            <BrandMark size={40} />
            <div>
              <p className="signin-story__name">{t("appName")}</p>
              <p className="signin-story__tagline">{t("brandTagline")}</p>
            </div>
          </div>
          <RuleStrip reduced={reduced} />
          <div className="signin-identity">
            <BrandMark size={48} className="signin-identity__mark" />
            <span className="portal-pill"><ShieldCheck size={14} aria-hidden="true" />{t("portalPill")}</span>
          </div>
          <StatusStrip state={status} />
          {reduced
            ? <div key={view}>{panel}</div>
            : <AnimatePresence mode="wait" initial={false}>
              <m.div key={view} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0, transition: { duration: DURATION.base, ease: EASE } }} exit={{ opacity: 0, transition: { duration: DURATION.fast, ease: EASE } }}>
                {panel}
              </m.div>
            </AnimatePresence>}
          <footer className="signin-footer">
            <span><ShieldCheck size={14} aria-hidden="true" />{fill(t("lockoutNote"), { count: LOCKOUT_ATTEMPTS })}</span>
            <span><WifiOff size={14} aria-hidden="true" />{t("offlineFooter")}</span>
          </footer>
        </div>
      </main>
    </div>
  );
}

type FormsProps = AuthPageProps & { mode: "login" | "register"; portal: Portal; onPortalChange: (portal: Portal) => void; focusHeading: boolean; onShow: (mode: "login" | "register") => void };

/** Sign-in and manufacturer registration, sharing one column. */
function AccountForms({ mode, portal, onPortalChange, focusHeading, loading, error, onLogin, onRegister, onShow }: FormsProps) {
  const { t } = useI18n();
  const registering = mode === "register";
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(false);
  const [loginData, setLoginData] = useState({ username: "", password: "" });
  const [registration, setRegistration] = useState<Registration>({ username: "", email: "", password: "", confirmPassword: "" });
  const [validation, setValidation] = useState("");
  // Set when the account belongs to the other portal; it names the account's own role. Never set for a failed sign-in.
  const [wrongPortal, setWrongPortal] = useState<Portal | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  // Each view mounts afresh; focus its heading unless this is the page's first view.
  useEffect(() => { if (focusHeading) heading.current?.focus({ preventScroll: true }); }, [mode, focusHeading]);

  function switchTo(next: "login" | "register") { setValidation(""); setShowPassword(false); onShow(next); }

  function selectPortal(next: Portal) { setWrongPortal(null); onPortalChange(next); }

  async function submit(event: FormEvent) {
    event.preventDefault(); setValidation(""); setWrongPortal(null);
    if (!registering) {
      const actual = await onLogin(loginData.username, loginData.password, remember, portal);
      if (actual) { setWrongPortal(actual); onPortalChange(actual); }
      return;
    }
    if (registration.password !== registration.confirmPassword) return setValidation(t("passwordsDoNotMatch"));
    if (!registration.email.includes("@") || registration.username.length < 3 || registration.password.length < 8) return setValidation(t("invalidRegistration"));
    if (registration.password.trim().toLowerCase() === registration.username.trim().toLowerCase()) return setValidation(t("passwordSameAsUsername"));
    onRegister(registration);
  }
  const passwordType = showPassword ? "text" : "password";
  const message = validation || (wrongPortal ? t(wrongPortal === "manufacturer" ? "loginWrongManufacturer" : "loginWrongInspector") : "") || error;

  return (
    <div className="signin-step">
      {registering && (
        <button type="button" className="signin-link signin-back" onClick={() => switchTo("login")}><ArrowLeft size={16} aria-hidden="true" />{t("backToSignIn")}</button>
      )}
      {!registering && (
        <Tabs stretch className="signin-tabs" label={t("loginRoleTabs")} value={portal} onChange={selectPortal}
          items={[{ value: "inspector", label: t("roleInspector") }, { value: "manufacturer", label: t("roleManufacturer") }]} />
      )}
      <div className="signin-heading">
        <h1 className="signin-title" ref={heading} tabIndex={-1}>{registering ? t("createAccount") : t(portal === "manufacturer" ? "loginAsManufacturer" : "loginAsInspector")}</h1>
        <p className="signin-subtitle">{registering ? t("manufacturerRegistrationNote") : t(portal === "manufacturer" ? "loginManufacturerHint" : "loginInspectorHint")}</p>
      </div>
      <form className="signin-form" onSubmit={submit}>
        <IconField icon={<User size={16} />} label={t("username")} id="auth-username">
          <Input id="auth-username" className="signin-input" autoComplete="username" required minLength={registering ? 3 : 1} value={registering ? registration.username : loginData.username}
            onChange={e => registering ? setRegistration({ ...registration, username: e.target.value }) : setLoginData({ ...loginData, username: e.target.value })} />
        </IconField>
        {registering && (
          <IconField icon={<Mail size={16} />} label={t("email")} id="auth-email">
            <Input id="auth-email" className="signin-input" type="email" autoComplete="email" required value={registration.email} onChange={e => setRegistration({ ...registration, email: e.target.value })} />
          </IconField>
        )}
        <IconField icon={<Lock size={16} />} label={t("password")} id="auth-password"
          labelAside={registering ? undefined : <a className="signin-link" href={`#${FORGOT_PASSWORD_PATH}`}>{t("forgotPassword")}</a>}>
          <Input id="auth-password" className="signin-input icon-input__control--toggle" type={passwordType} autoComplete={registering ? "new-password" : "current-password"} required minLength={registering ? 8 : 1}
            value={registering ? registration.password : loginData.password}
            onChange={e => registering ? setRegistration({ ...registration, password: e.target.value }) : setLoginData({ ...loginData, password: e.target.value })} />
          <PasswordToggle shown={showPassword} onToggle={() => setShowPassword(!showPassword)} />
        </IconField>
        {registering && (
          <IconField icon={<Lock size={16} />} label={t("confirmPassword")} id="auth-confirm">
            <Input id="auth-confirm" className="signin-input" type={passwordType} autoComplete="new-password" required minLength={8} value={registration.confirmPassword} onChange={e => setRegistration({ ...registration, confirmPassword: e.target.value })} />
          </IconField>
        )}
        {!registering && (
          <label className="signin-check">
            <input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)} />
            <span>{t("keepSignedIn")}</span>
          </label>
        )}
        {message && <Alert>{message}</Alert>}
        <Button type="submit" className="signin-submit" loading={loading}>
          {loading ? registering ? t("registering") : t("signingIn") : registering ? t("registerAccount") : <>{t("signIn")}<ArrowRight className="signin-submit__arrow" size={16} aria-hidden="true" /></>}
        </Button>
      </form>
      {!registering && (
        <>
          <p className="signin-divider"><span>{t("newManufacturer")}</span></p>
          <Button variant="outline" className="signin-secondary" onClick={() => switchTo("register")}>{t("createManufacturerAccount")}</Button>
        </>
      )}
    </div>
  );
}
