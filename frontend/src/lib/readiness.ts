import type { Readiness } from "../types";

/** Status colours stay semantic in both portals: ready = green, fix = red, check on the pack = amber. */
export const READINESS_TONE: Record<Readiness, "pass" | "violation" | "review"> = { READY: "pass", FIX: "violation", CHECK: "review" };

export function readinessOf(fixes: number, review: number): Readiness {
  return fixes > 0 ? "FIX" : review > 0 ? "CHECK" : "READY";
}

/** Verdict of an inspection history row, as the manufacturer reads it. */
export function readinessFromVerdict(verdict: string | null | undefined): Readiness | null {
  return verdict === "NON_COMPLIANT" ? "FIX" : verdict === "NEEDS_REVIEW" ? "CHECK" : verdict === "COMPLIANT" ? "READY" : null;
}

/** "Rule 6(1)(c)" out of "LM(PC) Rules 2011, Rule 6(1)(c) and Rule 9"; null for a reference without a rule number. */
export function ruleChip(reference: string | null | undefined): string | null {
  return /Rule\s+\d+(?:\(\w+\))*/.exec(reference ?? "")?.[0] ?? null;
}
