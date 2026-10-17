/**
 * Build script: bundles each TypeScript entry point into dist/ and copies static files.
 * Usage: `node build.mjs`, `node build.mjs --watch`, or `node build.mjs --release`
 * (clean dist/, no source maps: the build to zip for the Chrome Web Store).
 */
import * as esbuild from "esbuild";
import { cpSync, mkdirSync, rmSync } from "node:fs";

const watch = process.argv.includes("--watch");
const release = process.argv.includes("--release");

if (release) rmSync("dist", { recursive: true, force: true });
mkdirSync("dist", { recursive: true });
cpSync("static", "dist", { recursive: true });

const options = {
  entryPoints: {
    background: "src/background.ts",
    content: "src/content.ts",
    "main-world": "src/main-world.ts",
    sidepanel: "src/sidepanel.ts",
  },
  bundle: true,
  format: "iife",
  target: "chrome120",
  outdir: "dist",
  sourcemap: release ? false : "inline",
  logLevel: "info",
};

if (watch) {
  const ctx = await esbuild.context(options);
  await ctx.watch();
} else {
  await esbuild.build(options);
}
