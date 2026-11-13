import { cp, mkdir, rm } from "node:fs/promises";

await rm("dist", { recursive: true, force: true });
await mkdir("dist", { recursive: true });
await cp("static", "dist/static", { recursive: true });
await cp("static/index.html", "dist/index.html");
await cp("static/manifest.webmanifest", "dist/manifest.webmanifest");
await cp("static/sw.js", "dist/sw.js");

