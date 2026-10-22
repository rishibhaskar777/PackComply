import { lazy, ReactNode, Suspense, useEffect, useId, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { BookOpen, Boxes, ChevronsUpDown, CircleUserRound, ClipboardCheck, ClipboardList, Cpu, Factory, FileText, Info, LayoutDashboard, ListChecks, LogOut, LucideIcon, PanelLeft, Plus, Repeat2, ScanSearch, ShieldAlert, ShieldCheck, Smartphone, X } from "lucide-react";
import { LanguageSelector } from "../components/LanguageSelector";
import { Button, Dialog, SearchInput } from "../components/ui";
import { TranslationKey, useI18n } from "../i18n";
import { DURATION, EASE, SPRING, useReducedMotion } from "../lib/motion";
import { User } from "../types";
import { Backdrop } from "./Backdrop";
import { BrandMark } from "./BrandMark";
import { ThemeSwitcher, ThemeToggle } from "./ThemeControls";
import { portalOf } from "../lib/portal";
import { Route, RouteName } from "./router";
import { ShortcutsDialog, useShortcuts } from "./shortcuts";

type NavItem = { name: RouteName; path: string; label: TranslationKey; icon: LucideIcon };
const INSPECTION_NAV: NavItem[] = [
  { name: "overview", path: "/overview", label: "overview", icon: LayoutDashboard },
  { name: "inspections", path: "/inspections", label: "inspections", icon: ClipboardList },
  { name: "risk", path: "/risk", label: "riskQueue", icon: ShieldAlert },
  { name: "patterns", path: "/patterns", label: "repeatIssues", icon: Repeat2 },
];
const RESOURCE_NAV: NavItem[] = [
  { name: "rulesets", path: "/rulesets", label: "rulesets", icon: BookOpen },
  { name: "reports", path: "/reports", label: "reports", icon: FileText },
  { name: "about", path: "/about", label: "aboutAccuracy", icon: Info },
];
// Manufacturer portal: its own pages, not the inspector pages with parts hidden.
const MAKER_NAV: NavItem[] = [
  { name: "products", path: "/products", label: "myProducts", icon: Boxes },
  { name: "check", path: "/check", label: "checkALabel", icon: ScanSearch },
  { name: "fixes", path: "/fixes", label: "fixList", icon: ListChecks },
  { name: "reports", path: "/reports", label: "selfCheckReports", icon: FileText },
];
const MAKER_RESOURCE_NAV: NavItem[] = [
  { name: "guide", path: "/rules-guide", label: "rulesGuide", icon: BookOpen },
  { name: "about", path: "/about", label: "aboutAccuracy", icon: Info },
];

// The QR code library is only fetched when someone asks for the phone address.
const PhoneDialog = lazy(() => import("./PhoneDialog"));

/** Section and page names for the breadcrumb and document title. */
export function routeTitles(route: Route, role?: string): { section: TranslationKey; sectionHref?: string; page: TranslationKey } {
  if (role === "MANUFACTURER") {
    switch (route.name) {
      case "notfound": return { section: "labelSelfCheck", page: "notFoundTitle" };
      case "product": return { section: "myProducts", sectionHref: "#/products", page: "selfCheckResult" };
      case "check": case "new": case "capture": return { section: "myProducts", sectionHref: "#/products", page: "checkALabel" };
      case "fixes": return { section: "labelSelfCheck", page: "fixList" };
      case "reports": return { section: "labelSelfCheck", page: "selfCheckReports" };
      case "guide": return { section: "resources", page: "rulesGuide" };
      case "about": return { section: "resources", page: "aboutAccuracy" };
      default: return { section: "labelSelfCheck", page: "myProducts" };
    }
  }
  switch (route.name) {
    case "notfound": return { section: "inspection", page: "notFoundTitle" };
    case "inspection": return { section: "inspections", sectionHref: "#/inspections", page: "inspectionDetails" };
    case "new": case "capture": return { section: "inspections", sectionHref: "#/inspections", page: "newInspection" };
    case "rulesets": case "reports": return { section: "resources", page: route.name };
    case "about": return { section: "resources", page: "aboutAccuracy" };
    case "risk": return { section: "inspection", page: "riskQueue" };
    case "patterns": return { section: "inspection", page: "repeatIssues" };
    case "inspections": return { section: "inspection", page: "inspections" };
    default: return { section: "inspection", page: "overview" };
  }
}

/** The browser-tab title: the page's own name ("Today's inspection priorities", "My products"…), plus the record for a detail page. */
export function documentTitle(route: Route, role: string | undefined, t: (key: TranslationKey) => string, pageLabel?: string | null): string {
  const page = role !== "MANUFACTURER" && route.name === "overview" ? t("todaysPriorities") : t(routeTitles(route, role).page);
  const detail = (route.name === "inspection" || route.name === "product") && pageLabel ? `${pageLabel} · ` : "";
  return `${detail}${page} · ${t("appName")}`;
}

function activeNav(route: Route): RouteName {
  if (route.name === "product") return "products";
  return route.name === "inspection" || route.name === "new" || route.name === "capture" ? "inspections" : route.name;
}

function roleLabel(user: User, t: (key: TranslationKey) => string) {
  return user.role === "OFFICIAL" ? t("roleInspector") : user.role === "MANUFACTURER" ? t("roleManufacturer") : "";
}

export function initials(username: string) {
  const parts = username.split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : username.slice(0, 2)).toUpperCase();
}

const DRAWER_QUERY = "(max-width: 1023px)";
const COLLAPSE_KEY = "packcomply-sidebar-collapsed";
const isDrawerViewport = () => typeof window.matchMedia === "function" && window.matchMedia(DRAWER_QUERY).matches;

function readCollapsed() { try { return localStorage.getItem(COLLAPSE_KEY) === "1"; } catch { return false; } }

type AppShellProps = {
  /** Replaces the page name in the breadcrumb (e.g. the inspection number). */
  pageLabel?: string | null;
  route: Route; user: User; riskCount: number | null; fixCount?: number | null; search: string;
  onSearchChange: (value: string) => void; onNewInspection: () => void; onLogout: () => void; onExpired?: () => void; children: ReactNode;
};

export function AppShell({ pageLabel, route, user, riskCount, fixCount = null, search, onSearchChange, onNewInspection, onLogout, onExpired = () => undefined, children }: AppShellProps) {
  const { t } = useI18n();
  const reduced = useReducedMotion();
  const maker = user.role === "MANUFACTURER";
  const portal = portalOf(user.role);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [phoneOpen, setPhoneOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  useShortcuts({ maker, onNew: onNewInspection, onHelp: () => setShortcutsOpen(true) });
  const searchRef = useRef<HTMLInputElement>(null);
  const titles = routeTitles(route, user.role);
  const active = activeNav(route);

  // Close the drawer and the account sheet when the page changes. Not on mount: that effect can run after an early tap
  // on the tab bar and would close the sheet the tap just opened.
  const shownRoute = useRef(route);
  useEffect(() => {
    if (shownRoute.current === route) return;
    shownRoute.current = route;
    setDrawerOpen(false); setAccountOpen(false);
  }, [route]);
  useEffect(() => { try { localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0"); } catch { /* storage unavailable */ } }, [collapsed]);
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); searchRef.current?.focus(); }
      if (event.key === "Escape") setDrawerOpen(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  function toggleSidebar() { if (isDrawerViewport()) setDrawerOpen(open => !open); else setCollapsed(value => !value); }
  function onSearch(value: string) {
    onSearchChange(value);
    if (route.name !== "inspections") window.location.hash = "/inspections";
  }

  const navList = (items: NavItem[]) => (
    <ul className="nav-list">
      {items.map(({ name, path, label, icon: Icon }) => {
        const current = active === name;
        return (
          <li key={name}>
            <a href={`#${path}`} className={current ? "nav-item is-active" : "nav-item"} aria-current={current ? "page" : undefined} title={collapsed ? t(label) : undefined}>
              {/* One shared highlight that slides to the active item. */}
              {current && <m.span layoutId="nav-active" className="nav-item__highlight" transition={SPRING} aria-hidden="true" />}
              <Icon size={16} aria-hidden="true" />
              <span className="nav-item__label">{t(label)}</span>
              {name === "risk" && riskCount ? <span className="nav-item__count mono" aria-label={`${riskCount} ${t("highRiskLabels")}`}>{riskCount}</span> : null}
              {name === "fixes" && fixCount ? <span className="nav-item__count mono" aria-label={`${fixCount} ${t("fixItemsOpen")}`}>{fixCount}</span> : null}
            </a>
          </li>
        );
      })}
    </ul>
  );

  return (
    <div className={`shell ${collapsed ? "is-collapsed" : ""} ${drawerOpen ? "is-drawer-open" : ""}`.trim()}>
      <Backdrop />
      <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); document.getElementById("main-content")?.focus(); }}>{t("skipToContent")}</a>
      <aside className="sidebar" id="app-sidebar" aria-label={t("sidebar")}>
        <div className="sidebar__brand">
          <BrandMark />
          <div className="sidebar__brand-text">
            <span className="sidebar__product">{t("appName")}</span>
            <span className="sidebar__caption sidebar__portal">
              {maker ? <Factory size={12} aria-hidden="true" /> : <ShieldCheck size={12} aria-hidden="true" />}
              {t(maker ? "portalManufacturerCaption" : "portalInspectorCaption")}
            </span>
          </div>
          <Button variant="ghost" size="icon" className="sidebar__close" aria-label={t("closeMenu")} onClick={() => setDrawerOpen(false)} icon={<X size={16} aria-hidden="true" />} />
        </div>
        <nav className="sidebar__nav" aria-label={t("primaryNavigation")}>
          <p className="sidebar__group">{t(maker ? "labelSelfCheck" : "inspection")}</p>
          {navList(maker ? MAKER_NAV : INSPECTION_NAV)}
          <p className="sidebar__group">{t("resources")}</p>
          {navList(maker ? MAKER_RESOURCE_NAV : RESOURCE_NAV)}
        </nav>
        <div className="sidebar__footer">
          {maker
            ? <div className="offline-card offline-card--maker">
              <ClipboardCheck size={16} aria-hidden="true" className="offline-card__icon" />
              <p><strong>{t("checkBeforePrint")}</strong> — {t("checkBeforePrintDetail")}</p>
            </div>
            : <div className="offline-card">
              <Cpu size={16} aria-hidden="true" className="offline-card__icon" />
              <p><strong>{t("runsOffline")}</strong> — {t("runsOfflineDetail")}</p>
            </div>}
          <UserMenu user={user} onLogout={onLogout} />
        </div>
      </aside>
      <AnimatePresence>
        {drawerOpen && <m.button type="button" className="shell__backdrop" aria-label={t("closeMenu")} onClick={() => setDrawerOpen(false)}
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: reduced ? 0 : DURATION.base, ease: EASE }} />}
      </AnimatePresence>

      <div className="shell__main">
        <header className="topbar">
          <Button variant="ghost" size="icon" onClick={toggleSidebar} aria-controls="app-sidebar" aria-expanded={drawerOpen || !collapsed}
            aria-label={t("toggleSidebar")} icon={<PanelLeft size={16} aria-hidden="true" />} />
          <nav className="breadcrumb" aria-label={t("breadcrumb")}>
            <ol>
              <li className="breadcrumb__section">{titles.sectionHref ? <a href={titles.sectionHref}>{t(titles.section)}</a> : t(titles.section)}</li>
              <li aria-current="page" className={pageLabel && route.name === "inspection" ? "mono" : undefined}>{pageLabel ?? t(titles.page)}</li>
            </ol>
          </nav>
          <span className="portal-pill" data-portal-pill={portal}>{maker ? <Factory size={12} aria-hidden="true" /> : <ShieldCheck size={12} aria-hidden="true" />}{t(maker ? "portalManufacturer" : "portalInspector")}</span>
          <div className="topbar__spacer" />
          <ThemeToggle />
          <Button variant="outline" className="topbar__phone" icon={<Smartphone size={16} aria-hidden="true" />} onClick={() => setPhoneOpen(true)}>{t("phoneOpenButton")}</Button>
          {!maker && <SearchInput ref={searchRef} className="topbar__search" aria-label={t("searchInspections")} placeholder={t("searchPlaceholder")} hint="Ctrl K" value={search} onChange={event => onSearch(event.target.value)} />}
          <Button className="topbar__new" icon={<Plus size={16} aria-hidden="true" />} onClick={onNewInspection}>{t(maker ? "checkALabel" : "newInspection")}</Button>
        </header>
        <main id="main-content" className="shell__content" tabIndex={-1}>{children}</main>
      </div>

      <ShortcutsDialog open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} maker={maker} />
      {phoneOpen && <Suspense fallback={null}><PhoneDialog open onClose={() => setPhoneOpen(false)} onExpired={onExpired} /></Suspense>}

      <nav className="tabbar" aria-label={t("mobileNavigation")}>
        {maker ? <>
          <TabLink href="#/products" current={active === "products"} icon={<Boxes size={20} aria-hidden="true" />} label={t("tabProducts")} />
          <TabLink href="#/fixes" current={active === "fixes"} icon={<ListChecks size={20} aria-hidden="true" />} label={t("tabFixes")} badge={fixCount} badgeLabel={t("fixItemsOpen")} />
        </> : <>
          <TabLink href="#/overview" current={active === "overview"} icon={<LayoutDashboard size={20} aria-hidden="true" />} label={t("overview")} />
          <TabLink href="#/inspections" current={active === "inspections" && route.name !== "new" && route.name !== "capture"} icon={<ClipboardList size={20} aria-hidden="true" />} label={t("inspections")} />
        </>}
        <div className="tabbar__center"><button type="button" className="tabbar__fab" onClick={onNewInspection} aria-label={t(maker ? "checkALabel" : "newInspection")}><Plus size={26} aria-hidden="true" /></button></div>
        {maker
          ? <TabLink href="#/reports" current={active === "reports"} icon={<FileText size={20} aria-hidden="true" />} label={t("tabReports")} />
          : <TabLink href="#/risk" current={active === "risk"} icon={<ShieldAlert size={20} aria-hidden="true" />} label={t("riskQueue")} />}
        <button type="button" className={accountOpen ? "tabbar__item is-active" : "tabbar__item"} aria-haspopup="dialog" aria-expanded={accountOpen} onClick={() => setAccountOpen(true)}><CircleUserRound size={20} aria-hidden="true" /><span>{t("account")}</span></button>
      </nav>
      <Dialog open={accountOpen} onClose={() => setAccountOpen(false)} title={t("account")} className="account-sheet">
        <div className="account-sheet__body">
          <div className="account-sheet__who">
            <span className="avatar" aria-hidden="true">{initials(user.username)}</span>
            <span className="account-sheet__text">
              <span className="account-sheet__name">{user.full_name || user.username}</span>
              <span className="account-sheet__role">{roleLabel(user, t)}</span>
            </span>
          </div>
          <ThemeSwitcher />
          <LanguageSelector />
          <Button variant="outline" className="account-sheet__logout" icon={<LogOut size={16} aria-hidden="true" />} onClick={() => { setAccountOpen(false); onLogout(); }}>{t("logout")}</Button>
        </div>
      </Dialog>
    </div>
  );
}

/** "99+" above 99 so the count always fits the badge. */
export const badgeText = (count: number) => (count > 99 ? "99+" : String(count));

function TabLink({ href, current, icon, label, badge, badgeLabel }: { href: string; current: boolean; icon: ReactNode; label: string; badge?: number | null; badgeLabel?: string }) {
  const count = badge && badge > 0 ? badge : 0;
  return (
    <a href={href} className={current ? "tabbar__item is-active" : "tabbar__item"} aria-current={current ? "page" : undefined} aria-label={count && badgeLabel ? `${label}, ${count} ${badgeLabel}` : undefined}>
      <span className="tabbar__icon">{icon}{count ? <span className="tabbar__badge mono" aria-hidden="true">{badgeText(count)}</span> : null}</span>
      <span>{label}</span>
    </a>
  );
}

function UserMenu({ user, onLogout }: { user: User; onLogout: () => void }) {
  const { t } = useI18n();
  const reduced = useReducedMotion();
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const container = useRef<HTMLDivElement>(null);
  const role = user.role === "OFFICIAL" ? t("roleInspector") : user.role === "MANUFACTURER" ? t("roleManufacturer") : null;

  useEffect(() => {
    if (!open) return;
    function onPointer(event: PointerEvent) { if (!container.current?.contains(event.target as Node)) setOpen(false); }
    function onKey(event: KeyboardEvent) { if (event.key === "Escape") setOpen(false); }
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onPointer); document.removeEventListener("keydown", onKey); };
  }, [open]);

  return (
    <div className="user-menu" ref={container}>
      <AnimatePresence>
        {open && (
          <m.div className="user-menu__panel" id={menuId}
            initial={reduced ? false : { opacity: 0, y: 6, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduced ? { opacity: 0, transition: { duration: 0 } } : { opacity: 0, y: 6, scale: 0.98, transition: { duration: DURATION.fast } }}
            transition={{ duration: DURATION.base, ease: EASE }}>
            <ThemeSwitcher />
            <LanguageSelector />
            <hr className="user-menu__separator" />
            <Button variant="ghost" className="user-menu__logout" icon={<LogOut size={16} aria-hidden="true" />} onClick={onLogout}>{t("logout")}</Button>
          </m.div>
        )}
      </AnimatePresence>
      <button type="button" className="user-menu__button" aria-expanded={open} aria-controls={menuId} aria-label={`${t("accountMenu")}: ${user.username}`} onClick={() => setOpen(value => !value)}>
        <span className="avatar" aria-hidden="true">{initials(user.username)}</span>
        <span className="user-menu__text">
          <span className="user-menu__name">{user.username}</span>
          {role && <span className="user-menu__role">{role}</span>}
        </span>
        <ChevronsUpDown size={16} aria-hidden="true" className="user-menu__chevron" />
      </button>
    </div>
  );
}
