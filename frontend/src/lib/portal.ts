import { useLayoutEffect } from "react";

/** The two products inside PackComply. Which one a user sees follows their role; the server enforces the data side. */
export type Portal = "inspector" | "manufacturer";

export const portalOf = (role?: string | null): Portal => (role === "MANUFACTURER" ? "manufacturer" : "inspector");

/** Sets <html data-portal="…"> from the signed-in role (see the portal layer in styles/tokens.css) and clears it on sign-out (the effect cleanup). The sign-in page sets its own portal from the selected tab. */
export function usePortalAttribute(role: string | null | undefined, signedIn: boolean) {
  useLayoutEffect(() => {
    const root = document.documentElement;
    if (signedIn) root.dataset.portal = portalOf(role);
    return () => { delete root.dataset.portal; };
  }, [role, signedIn]);
}
