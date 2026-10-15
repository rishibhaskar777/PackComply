import { FormEvent, useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { Backdrop } from "../app/Backdrop";
import { BrandMark } from "../app/BrandMark";
import { ThemeToggle } from "../app/ThemeControls";
import { LanguageSelector } from "../components/LanguageSelector";
import { Alert, Button, FormField, Input, Tabs } from "../components/ui";
import { useI18n } from "../i18n";

export type Registration = { username: string; email: string; password: string; confirmPassword: string };
type AuthPageProps = { loading: boolean; error: string; onLogin: (username: string, password: string) => void; onRegister: (data: Registration) => void };

export function AuthPage({ loading, error, onLogin, onRegister }: AuthPageProps) {
  const { t } = useI18n();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [showPassword, setShowPassword] = useState(false);
  const [loginData, setLoginData] = useState({ username: "", password: "" });
  const [registration, setRegistration] = useState<Registration>({ username: "", email: "", password: "", confirmPassword: "" });
  const [validation, setValidation] = useState("");
  const registering = mode === "register";

  function submit(event: FormEvent) {
    event.preventDefault(); setValidation("");
    if (!registering) return onLogin(loginData.username, loginData.password);
    if (registration.password !== registration.confirmPassword) return setValidation(t("passwordsDoNotMatch"));
    if (!registration.email.includes("@") || registration.username.length < 3 || registration.password.length < 8) return setValidation(t("invalidRegistration"));
    onRegister(registration);
  }
  const passwordType = showPassword ? "text" : "password";
  const message = validation || error;

  return (
    <div className="auth-page">
      <Backdrop variant="auth" />
      <div className="auth-page__controls"><ThemeToggle /><LanguageSelector compact /></div>
      <main className="auth-page__main">
        <div className="auth-brand">
          <BrandMark size={40} className="brand-mark--float" />
          <span className="auth-brand__name">{t("appName")}</span>
          <span className="auth-brand__caption">{t("legalMetrology")}</span>
        </div>
        <div className="auth-stage">
          <span className="auth-ring" aria-hidden="true" />
          <section className="auth-card" aria-labelledby="auth-title">
            <div className="auth-card__body">
              <Tabs stretch label={t("account")} value={mode} onChange={value => { setMode(value); setValidation(""); }} items={[{ value: "login", label: t("login") }, { value: "register", label: t("register") }]} />
              <div className="auth-card__heading">
                <h1 id="auth-title" className="auth-card__title">{registering ? t("createAccount") : t("signInTitle")}</h1>
                <p className="auth-card__description">{registering ? t("manufacturerRegistrationNote") : t("signInDescription")}</p>
              </div>
              <form className="auth-form" onSubmit={submit} noValidate={false}>
                <FormField label={t("username")} htmlFor="auth-username">
                  <Input id="auth-username" autoComplete="username" required minLength={registering ? 3 : 1} value={registering ? registration.username : loginData.username}
                    onChange={e => registering ? setRegistration({ ...registration, username: e.target.value }) : setLoginData({ ...loginData, username: e.target.value })} />
                </FormField>
                {registering && <FormField label={t("email")} htmlFor="auth-email">
                  <Input id="auth-email" type="email" autoComplete="email" required value={registration.email} onChange={e => setRegistration({ ...registration, email: e.target.value })} />
                </FormField>}
                <FormField label={t("password")} htmlFor="auth-password">
                  <span className="password-input">
                    <Input id="auth-password" type={passwordType} autoComplete={registering ? "new-password" : "current-password"} required minLength={registering ? 8 : 1} value={registering ? registration.password : loginData.password}
                      onChange={e => registering ? setRegistration({ ...registration, password: e.target.value }) : setLoginData({ ...loginData, password: e.target.value })} />
                    <Button variant="ghost" size="icon" className="password-input__toggle" aria-label={showPassword ? t("hidePassword") : t("showPassword")} aria-pressed={showPassword} onClick={() => setShowPassword(!showPassword)}
                      icon={showPassword ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />} />
                  </span>
                </FormField>
                {registering && <FormField label={t("confirmPassword")} htmlFor="auth-confirm">
                  <Input id="auth-confirm" type={passwordType} autoComplete="new-password" required minLength={8} value={registration.confirmPassword} onChange={e => setRegistration({ ...registration, confirmPassword: e.target.value })} />
                </FormField>}
                {message && <Alert>{message}</Alert>}
                <Button type="submit" className="auth-form__submit" loading={loading}>
                  {loading ? registering ? t("registering") : t("signingIn") : registering ? t("registerAccount") : t("signIn")}
                </Button>
              </form>
            </div>
          </section>
        </div>
        <p className="auth-page__footnote">{t("offlineNoticeShort")}</p>
      </main>
    </div>
  );
}
