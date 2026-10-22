import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
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
const { recordedMapPngUrl } = await import("../assets/js/demo_dichromatic.js");

test("only the published project is available, with no pending write actions", async () => {
  assert.deepEqual((await (await api("/projects")).json()).projects.map((project) => project.project_id), ["Demo-GPT"]);
  assert.equal((await api("/projects/private-project")).status, 404);
  assert.equal((await (await api("/projects/Demo-GPT/memory")).json()).pending_plan, null);
  for (const [route, method] of [["/projects", "POST"], ["/projects/Demo-GPT", "DELETE"], ["/chat/stream", "POST"]]) {
    assert.equal((await api(route, { method })).status, 405);
  }
});

test("the replacement study starts with its own CSL screening conversation", async () => {
  const history = await (await api("/projects/Demo-GPT/history?limit=5")).json();
  const expectedIds = [
    "ba1d891a-5433-41f3-ad76-363f75c80754",
    "1a210ca3-6289-4dc5-b1b8-7a229695e1e2",
    "e51fb8fc-29de-495b-8e59-be14d712be23",
    "fa6ebca2-a0b7-4c40-bf26-1cfe371f785f",
  ];
  assert.equal(data.stats.turns, 30);
  assert.deepEqual(history.turns.slice(0, 4).map((turn) => turn.turn_id), expectedIds);
  assert.deepEqual(history.turns.slice(0, 4).map((turn) => turn.role), ["user", "assistant", "user", "assistant"]);
  assert.deepEqual(history.turns.slice(0, 4).map((turn) => turn.sequence), [0, 1, 2, 3]);
  assert.ok(history.turns.every((turn) => turn.thread_id === data.project.thread_id));
  assert.equal(history.turns[4].turn_id, "704318a9-ba48-48c1-bbc5-ed9508cd782d");
  assert.equal(history.turns[4].sequence, 4);
  let events = 0;
  for (const turn of history.turns.slice(0, 4)) {
    const detail = await (await api(`/projects/Demo-GPT/history/${turn.turn_id}`)).json();
    assert.equal(detail.content, turn.content);
    assert.ok(detail.content.trim());
    assert.ok(detail.trace.every((event) => !/(?:^|_)plans?(?:_|$)/i.test(event.tool_name || "")));
    events += detail.trace.length;
  }
  assert.ok(events > 20);
});

test("pagination and lazy turn details preserve complete recorded trace relationships", async () => {
  const first = await (await api("/projects/Demo-GPT/history?limit=5")).json();
  assert.equal(first.turns.length, 5);
  assert.equal(first.has_more, true);
  const remaining = await (await api("/projects/Demo-GPT/history?offset=5&limit=200")).json();
  assert.equal(remaining.turns.length, data.stats.turns - 5);
  assert.equal(remaining.has_more, false);
  let eventCount = 0;
  let toolCount = 0;
  const allTurns = [...first.turns, ...remaining.turns];
  assert.deepEqual(allTurns.map((turn) => turn.sequence), Array.from({ length: 30 }, (_, index) => index));
  assert.equal(allTurns[24].turn_id, "d2d92b5a-6db8-4936-b570-f0e8168b9119");
  assert.equal(allTurns[26].content, "Apply strain to my saved selection");
  assert.equal(allTurns.filter((turn) => turn.content.startsWith("I confirmed the near-CSL unit cell in the DichromaticMap widget.")).length, 1);
  for (const turn of allTurns) {
    const detail = await (await api(`/projects/Demo-GPT/history/${turn.turn_id}`)).json();
    assert.equal(detail.content, turn.content);
    const ids = new Set(detail.trace.map((event) => event.event_id));
    for (const event of detail.trace) {
      if (event.parent_event_id) assert.ok(ids.has(event.parent_event_id));
      if (event.event_type === "tool_start") toolCount += 1;
    }
    eventCount += detail.trace.length;
  }
  assert.equal(eventCount, 443);
  assert.equal(toolCount, data.stats.tool_calls);
  assert.equal(toolCount, 193);
  assert.equal((await api("/projects/Demo-GPT/history/unknown")).status, 404);
});

test("lazy traces use workspace preview limits and retain images beyond the text preview", async () => {
  const history = await (await api("/projects/Demo-GPT/history")).json();
  const delegateTools = new Set(["delegate_plan", "delegate_csl", "delegate_lammps", "delegate_analysis", "delegate_coding"]);
  let ordinaryOutputs = 0;
  let delegateOutputs = 0;
  let imageAfterPreview = false;
  for (const turn of history.turns) {
    const detail = await (await api(`/projects/Demo-GPT/history/${turn.turn_id}`)).json();
    for (const event of detail.trace) {
      const delegate = delegateTools.has(event.tool_name);
      if (event.event_type === "tool_start") {
        assert.ok(Array.from(event.input_json || "").length <= (delegate ? 1200 : 280));
      }
      if (event.event_type === "tool_end") {
        const output = event.output_text || "";
        assert.ok(Array.from(output).length <= (delegate ? 8000 : 400));
        if (!delegate) ordinaryOutputs += 1;
        else delegateOutputs += 1;
        for (const image of event.image_paths) {
          assert.ok(data.files[image]);
          if (!output.includes(image)) imageAfterPreview = true;
        }
      }
    }
  }
  assert.equal(ordinaryOutputs, 170);
  assert.equal(delegateOutputs, 23);
  assert.ok(imageAfterPreview, "An image omitted from the preview still has an explicit reference.");
});

test("directory entries resolve to published files and text previews stay bounded", async () => {
  const root = await (await api("/projects/Demo-GPT/files")).json();
  assert.ok(root.entries.some((entry) => entry.path === "artifacts" && entry.entry_type === "dir"));
  assert.ok(root.entries.every((entry) => entry.watch === false));
  const path = "memory/summary.md";
  const preview = await (await api(`/projects/Demo-GPT/files/content?path=${encodeURIComponent(path)}`)).json();
  assert.ok(preview.content.includes("P012"));
  assert.equal(preview.watch, false);
  const large = Object.values(data.files).find((entry) => entry.is_text && entry.size > 262144);
  const limited = await (await api(`/projects/Demo-GPT/files/content?path=${encodeURIComponent(large.path)}`)).json();
  assert.ok(limited.content.endsWith("[Preview limited to 256 KB. Download the file to read the complete data.]"));
  assert.ok(limited.content.length <= 262244);
  for (const entry of Object.values(data.files)) {
    assert.equal((await stat(fileURLToPath(projectFileUrl("Demo-GPT", entry.path)))).size, entry.size);
  }
});

test("cleaned file APIs retain all NEB endpoints and publish one shared potential", async () => {
  const nebBase = "artifacts/lammps/neb/neb-20261021-214036-574393";
  const minimizeBase = "artifacts/lammps/structure_minimize/structure-minimize-20261021-220732-c86c47";
  const sharedPath = `${minimizeBase}/inputs/potential_NiCr.adp`;
  const downloadJson = async (path) => (await api(`/projects/Demo-GPT/files/download?path=${encodeURIComponent(path)}`)).json();
  const neb = await downloadJson(`${nebBase}/manifest.json`);
  const minimize = await downloadJson(`${minimizeBase}/manifest.json`);
  assert.equal(neb.omitted_from_demo.length, 18);
  assert.equal(minimize.deduplicated_from_demo.length, 17);
  assert.equal(minimize.shared_inputs.length, 1);
  for (let index = 0; index < 18; index += 1) {
    const path = `${nebBase}/neb.replica.${index}.final.lmp`;
    assert.ok(data.files[path], `Missing NEB endpoint ${index}`);
    assert.equal((await api(`/projects/Demo-GPT/files/stat?path=${encodeURIComponent(path)}`)).status, 200);
  }
  assert.deepEqual(Object.keys(data.files).filter((path) => path.endsWith("/potential_NiCr.adp")), [sharedPath]);
  const response = await api(`/projects/Demo-GPT/files/download?path=${encodeURIComponent(sharedPath)}`);
  assert.equal(response.status, 200);
  const hash = createHash("sha256").update(Buffer.from(await response.arrayBuffer())).digest("hex");
  assert.equal(hash, "4bc730061b4df3475b4330954a7b6322f6d133ea2e9cb9c7c59719eac72efb9f");
  const originalPaths = [
    ...neb.omitted_from_demo.map((entry) => `${nebBase}/${entry.path}`),
    ...minimize.deduplicated_from_demo.map((entry) => `${minimizeBase}/${entry.path}`),
    `${minimizeBase}/${minimize.shared_inputs[0].original_path}`,
  ];
  assert.equal(new Set(originalPaths).size, 36);
  const directoryPaths = new Set(Object.values(data.directories).flat().map((entry) => entry.path));
  for (const path of originalPaths) {
    assert.equal(data.files[path], undefined, `Removed input is still offered: ${path}`);
    assert.equal(directoryPaths.has(path), false, `Removed input is still listed: ${path}`);
    assert.equal((await api(`/projects/Demo-GPT/files/download?path=${encodeURIComponent(path)}`)).status, 404);
  }
  assert.equal(data.stats.files, Object.keys(data.files).length);
});

test("paths stay inside Demo-GPT and unsupported or binary previews fail clearly", async () => {
  for (const path of ["../project.json", "/project.json", "artifacts/../project.json", "artifacts\\file", "artifacts//file"]) {
    assert.equal(projectFileUrl("Demo-GPT", path), "");
    assert.equal((await api(`/projects/Demo-GPT/files?path=${encodeURIComponent(path)}`)).status, 400);
  }
  assert.equal(projectFileUrl("another-project", "project.json"), "");
  assert.equal((await api("/projects/Demo-GPT/files/content?path=chat/history.sqlite")).status, 415);
  assert.equal((await api("/projects/Demo-GPT/files?path=missing")).status, 404);
});

test("all displayed gallery and map assets exist", async () => {
  assert.equal(data.maps.length, 1);
  assert.equal(data.gallery.length, 8);
  for (const item of [...data.gallery, ...data.maps]) {
    assert.ok(data.files[item.path]);
    await stat(fileURLToPath(projectFileUrl("Demo-GPT", item.path)));
  }
  for (const item of data.maps) {
    const diagram = JSON.parse(await readFile(fileURLToPath(projectFileUrl("Demo-GPT", item.path)), "utf8"));
    const pngUrl = recordedMapPngUrl(diagram, "Demo-GPT");
    assert.ok(pngUrl);
    await stat(fileURLToPath(pngUrl));
    assert.equal(recordedMapPngUrl(diagram, "another-project"), "");
  }
});

test("static image URLs normalize consistently under a repository subpath", () => {
  const path = data.gallery[0].path;
  const origin = "https://zhugroup-gt.github.io";
  assert.equal(normalizeArtifactPath(`${origin}/GBClaw/projects/Demo-GPT/${path}`, "Demo-GPT", origin), path);
  assert.equal(normalizeArtifactPath(`${origin}/GBClaw/projects/another/${path}`, "Demo-GPT", origin), null);
  assert.equal(normalizeArtifactPath(`https://other.invalid/projects/Demo-GPT/${path}`, "Demo-GPT", origin), null);
  assert.equal(normalizeArtifactPath(`${origin}/GBClaw/projects/Demo-GPT/artifacts/../project.json`, "Demo-GPT", origin), null);
});

test("further pruning exposes canonical replacements and removes obsolete downloads", async () => {
  for (const projectId of ["Demo-GPT"]) {
    const demo = await loadDemo(projectId);
    const removedPaths = [];
    for (const run of demo.memory.runs) {
      const response = await api(`/projects/${projectId}/files/download?path=${encodeURIComponent(run.manifest_path)}`);
      const manifest = await response.json();
      const base = run.manifest_path.slice(0, run.manifest_path.lastIndexOf("/"));
      for (const record of manifest.pruned_from_demo || []) {
        const path = record.project_path || (record.path.startsWith("artifacts/") ? record.path : `${base}/${record.path}`);
        removedPaths.push(path);
        assert.equal(demo.files[path], undefined);
        assert.equal((await api(`/projects/${projectId}/files/download?path=${encodeURIComponent(path)}`)).status, 404);
        assert.ok(demo.files[record.replacement_path]);
        assert.equal((await api(`/projects/${projectId}/files/stat?path=${encodeURIComponent(record.replacement_path)}`)).status, 200);
      }
    }
    assert.ok(removedPaths.length >= 90);
    assert.equal(new Set(removedPaths).size, removedPaths.length);
    const directoryPaths = new Set(Object.values(demo.directories).flat().map((entry) => entry.path));
    for (const path of removedPaths) assert.equal(directoryPaths.has(path), false);
    assert.equal(demo.files["runtime/checkpoints.sqlite"], undefined);
    assert.ok(demo.files["chat/transcript.md"]);
  }
});
