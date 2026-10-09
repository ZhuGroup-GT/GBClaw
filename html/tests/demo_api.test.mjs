import test from "node:test";
import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";

// Use the same checked-in snapshots as a static HTTP server would serve.
globalThis.fetch = async (resource) => {
  const url = new URL(resource);
  const bytes = await readFile(fileURLToPath(url));
  return new Response(bytes);
};
const { demoFetch, loadDemo, projectFileUrl } = await import("../assets/js/demo_api.js");
const api = (route, options) => demoFetch(`http://demo.invalid/api${route}`, options);
const data = await loadDemo();
const { normalizeArtifactPath } = await import("../assets/js/artifact_images.js");

test("DemoProj is the only available project, with no pending write actions", async () => {
  assert.equal((await (await api("/projects")).json()).projects[0].project_id, "DemoProj");
  assert.equal((await api("/projects/private-project")).status, 404);
  assert.equal((await (await api("/projects/DemoProj/memory")).json()).pending_plan, null);
  for (const [route, method] of [["/projects", "POST"], ["/projects/DemoProj", "DELETE"], ["/chat/stream", "POST"]]) {
    assert.equal((await api(route, { method })).status, 405);
  }
});

test("pagination and lazy turn details preserve complete recorded trace relationships", async () => {
  const first = await (await api("/projects/DemoProj/history?limit=5")).json();
  assert.equal(first.turns.length, 5);
  assert.equal(first.has_more, true);
  const remaining = await (await api("/projects/DemoProj/history?offset=5&limit=200")).json();
  assert.equal(remaining.turns.length, data.stats.turns - 5);
  assert.equal(remaining.has_more, false);
  let eventCount = 0;
  let toolCount = 0;
  for (const turn of [...first.turns, ...remaining.turns]) {
    const detail = await (await api(`/projects/DemoProj/history/${turn.turn_id}`)).json();
    assert.equal(detail.content, turn.content);
    const ids = new Set(detail.trace.map((event) => event.event_id));
    for (const event of detail.trace) {
      if (event.parent_event_id) assert.ok(ids.has(event.parent_event_id));
      if (event.event_type === "tool_start") toolCount += 1;
    }
    eventCount += detail.trace.length;
  }
  assert.equal(eventCount, 389);
  assert.equal(toolCount, data.stats.tool_calls);
  assert.equal((await api("/projects/DemoProj/history/unknown")).status, 404);
});

test("directory entries resolve to published files and text previews stay bounded", async () => {
  const root = await (await api("/projects/DemoProj/files")).json();
  assert.ok(root.entries.some((entry) => entry.path === "artifacts" && entry.entry_type === "dir"));
  assert.ok(root.entries.every((entry) => entry.watch === false));
  const path = "memory/summary.md";
  const preview = await (await api(`/projects/DemoProj/files/content?path=${encodeURIComponent(path)}`)).json();
  assert.ok(preview.content.includes("P012"));
  assert.equal(preview.watch, false);
  const large = Object.values(data.files).find((entry) => entry.is_text && entry.size > 262144);
  const limited = await (await api(`/projects/DemoProj/files/content?path=${encodeURIComponent(large.path)}`)).json();
  assert.ok(limited.content.endsWith("[Preview limited to 256 KB. Download the file to read the complete data.]"));
  assert.ok(limited.content.length <= 262244);
  for (const entry of Object.values(data.files)) {
    assert.equal((await stat(fileURLToPath(projectFileUrl("DemoProj", entry.path)))).size, entry.size);
  }
});

test("paths stay inside DemoProj and unsupported or binary previews fail clearly", async () => {
  for (const path of ["../project.json", "/project.json", "artifacts/../project.json", "artifacts\\file", "artifacts//file"]) {
    assert.equal(projectFileUrl("DemoProj", path), "");
    assert.equal((await api(`/projects/DemoProj/files?path=${encodeURIComponent(path)}`)).status, 400);
  }
  assert.equal(projectFileUrl("another-project", "project.json"), "");
  assert.equal((await api("/projects/DemoProj/files/content?path=chat/history.sqlite")).status, 415);
  assert.equal((await api("/projects/DemoProj/files?path=missing")).status, 404);
});

test("all displayed gallery and map assets exist", async () => {
  assert.equal(data.maps.length, 2);
  for (const item of [...data.gallery, ...data.maps]) {
    assert.ok(data.files[item.path]);
    await stat(fileURLToPath(projectFileUrl("DemoProj", item.path)));
  }
});

test("static image URLs normalize consistently under a repository subpath", () => {
  const path = data.gallery[0].path;
  const origin = "https://zhugroup-gt.github.io";
  assert.equal(normalizeArtifactPath(`${origin}/GBClaw/projects/DemoProj/${path}`, "DemoProj", origin), path);
  assert.equal(normalizeArtifactPath(`${origin}/GBClaw/projects/another/${path}`, "DemoProj", origin), null);
  assert.equal(normalizeArtifactPath(`https://other.invalid/projects/DemoProj/${path}`, "DemoProj", origin), null);
  assert.equal(normalizeArtifactPath(`${origin}/GBClaw/projects/DemoProj/artifacts/../project.json`, "DemoProj", origin), null);
});
