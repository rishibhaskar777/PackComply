import { configure } from "@testing-library/react";

// The app lazy-loads its pages with import(), and vite-node compiles each chunk on first use. In a full parallel run a cold
// worker can need more than Testing Library's default 1000 ms for the first findBy*/waitFor, which made otherwise correct
// tests (e.g. the mobile tab bar Account sheet) fail only when the whole suite ran. Waits still return as soon as they pass.
configure({ asyncUtilTimeout: 8000 });
