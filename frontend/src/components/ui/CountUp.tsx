import { useCountUp } from "../../lib/motion";

/** A number that counts up from 0 on mount (about 800 ms, easing out); the final value at once under reduced motion. */
export function CountUp({ value }: { value: number }) {
  return <>{useCountUp(value)}</>;
}
