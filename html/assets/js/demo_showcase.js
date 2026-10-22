import { loadDemo, projectFileUrl } from "./demo_api.js?v=3";
import { renderMarkdown } from "./markdown.js?v=5";
import { enhanceArtifactImages } from "./artifact_images.js?v=6";
import { openImageModal } from "./image_modal.js?v=1";
import { initDemoDichromatic } from "./demo_dichromatic.js?v=4";

export function navigateDemo(section) {
  const target = { summary: "demo-overview", history: "demo-history", gallery: "demo-gallery", map: "demo-map" }[section];
  if (!target) return;
  document.getElementById(target)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function filterDemoHistory() {
  const query = document.getElementById("demo-history-search")?.value.trim().toLocaleLowerCase() || "";
  const messages = [...document.querySelectorAll("#chat-log > .message")];
  let matches = 0;
  for (const message of messages) {
    message.hidden = Boolean(query && !(message.dataset.searchText || "").includes(query));
    if (!message.hidden) matches += 1;
  }
  const status = document.getElementById("demo-history-status");
  if (status) status.textContent = query ? `${matches} of ${messages.length} recorded messages match.` : `${messages.length} recorded messages · execution traces load when opened.`;
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}

function renderPresentation(demo) {
  const id = demo.project.project_id;
  const showcase = demo.showcase || demo.project.showcase || {};
  const presentation = showcase.presentation || {};
  document.title = `GBClaw — ${id} Showcase`;
  setText("brand-tagline", `${id} showcase · Zhu Group at Georgia Tech`);
  setText("demo-project-eyebrow", `${id} · Recorded research workspace`);
  setText("welcome-title", `Explore ${id}`);
  setText("welcome-description", presentation.description || "A recorded grain boundary research workspace. Explore its conversation, project memory, and saved results.");
  setText("demo-overview-description", presentation.overview || "Browse this project's recorded workflow, research files, and analysis outputs.");
  setText("demo-history-description", `Read the requests, decisions, and responses that shaped ${id}. Search the original conversation to find a topic.`);
  setText("footer-text", `GBClaw © 2026 · Zhu Group, Georgia Tech · ${id} research showcase`);
  document.getElementById("chat-log")?.setAttribute("aria-label", `Recorded ${id} conversation`);

  const facts = document.getElementById("demo-facts");
  if (facts) {
    facts.replaceChildren();
    for (const fact of presentation.facts || []) {
      const group = document.createElement("div");
      const label = document.createElement("dt");
      label.textContent = fact.label;
      const value = document.createElement("dd");
      value.textContent = fact.value;
      if (fact.detail) {
        const detail = document.createElement("span");
        detail.textContent = fact.detail;
        value.appendChild(detail);
      }
      group.append(label, value);
      facts.appendChild(group);
    }
    facts.hidden = !facts.children.length;
  }
  const workflow = document.getElementById("demo-workflow");
  if (workflow) {
    workflow.replaceChildren();
    for (const stage of presentation.workflow || []) {
      const item = document.createElement("li");
      item.textContent = stage;
      workflow.appendChild(item);
    }
    workflow.hidden = !workflow.children.length;
  }
  const notice = document.getElementById("demo-file-notice");
  if (notice) {
    notice.textContent = presentation.file_notice || "";
    notice.hidden = !notice.textContent;
  }
  renderModels(showcase);
}

function renderModels(showcase) {
  const container = document.getElementById("demo-model-grid");
  if (!container) return;
  container.replaceChildren();
  const groups = new Map();
  const roles = { orchestrator: "Orchestrator", coding: "Coding", structure: "Structure", lammps: "LAMMPS", analysis: "Analysis" };
  for (const [role, model] of Object.entries(showcase.models || {})) {
    if (typeof model !== "string" || !model) continue;
    if (!groups.has(model)) groups.set(model, []);
    groups.get(model).push(roles[role] || role);
  }
  const card = (title, description) => {
    const item = document.createElement("article");
    item.className = "demo-model-card";
    const heading = document.createElement("h4");
    heading.textContent = title;
    const copy = document.createElement("p");
    copy.textContent = description;
    item.append(heading, copy);
    container.appendChild(item);
  };
  for (const [model, agents] of groups) {
    const throughout = groups.size === 1 && Object.keys(roles).every(role => showcase.models?.[role] === model);
    card(model, `${throughout ? "Used throughout the recorded study by " : ""}${agents.join(" + ")}`);
  }
  const section = document.getElementById("demo-models");
  if (section) section.hidden = !container.children.length;
}

function renderStats(demo) {
  const container = document.getElementById("demo-stats");
  if (!container) return;
  container.replaceChildren();
  const stats = demo.stats;
  for (const [value, label] of [[stats.turns, "Recorded messages"], [stats.tool_calls, "Tool calls"], [stats.runs, "Research runs"], [stats.files, "Project files"]]) {
    const item = document.createElement("div");
    item.className = "demo-stat";
    const number = document.createElement("strong");
    number.textContent = Number(value || 0).toLocaleString("en-US");
    const caption = document.createElement("span");
    caption.textContent = label;
    item.append(number, caption);
    container.appendChild(item);
  }
}

function renderMemory(demo) {
  const container = document.getElementById("demo-memory-content");
  if (!container) return;
  container.classList.add("markdown");
  container.innerHTML = renderMarkdown(demo.memory.summary || "No summary was recorded.");
  const projectId = demo.project.project_id;
  enhanceArtifactImages(container, { projectId, append: false,
    onOpenImage: (url, name) => openImageModal(url, name) });
  const source = document.createElement("a");
  source.href = projectFileUrl(projectId, "memory/summary.md");
  source.download = `${projectId}-summary.md`;
  source.textContent = "Download the recorded project summary";
  container.appendChild(source);
}

function renderGallery(demo) {
  const container = document.getElementById("demo-gallery-content");
  if (!container) return;
  container.replaceChildren();
  const projectId = demo.project.project_id;
  for (const result of demo.gallery || []) {
    const card = document.createElement("article");
    card.className = "demo-gallery-card";
    const imageButton = document.createElement("button");
    imageButton.type = "button";
    imageButton.className = "demo-gallery-image";
    imageButton.setAttribute("aria-label", `View ${result.title}`);
    const img = document.createElement("img");
    img.src = projectFileUrl(projectId, result.path);
    img.alt = result.title;
    img.loading = "lazy";
    img.addEventListener("error", () => { img.alt = `${result.title} — image unavailable; use the source link below.`; });
    imageButton.appendChild(img);
    imageButton.addEventListener("click", () => openImageModal(img.src, result.path.split("/").pop()));
    const caption = document.createElement("div");
    caption.className = "demo-gallery-caption";
    const title = document.createElement("h3");
    title.textContent = result.title;
    const description = document.createElement("p");
    description.textContent = result.description || (result.workflow || "Recorded result").replaceAll("_", " ");
    const source = document.createElement("a");
    source.href = projectFileUrl(projectId, result.path);
    source.download = result.path.split("/").pop();
    source.textContent = "Download image";
    caption.append(title, description, source);
    if (result.manifest_path) {
      const manifest = document.createElement("a");
      manifest.href = projectFileUrl(projectId, result.manifest_path);
      manifest.target = "_blank";
      manifest.rel = "noopener";
      manifest.textContent = "Run manifest";
      caption.appendChild(manifest);
    }
    card.append(imageButton, caption);
    container.appendChild(card);
  }
  if (!container.children.length) {
    const empty = document.createElement("p");
    empty.className = "demo-loading";
    empty.textContent = "No figures were recorded for this workspace.";
    container.appendChild(empty);
  }
}

export async function initDemoShowcase() {
  const demo = await loadDemo();
  const projectId = demo.project.project_id;
  renderPresentation(demo);
  renderStats(demo);
  renderMemory(demo);
  renderGallery(demo);
  document.getElementById("demo-history-search")?.addEventListener("input", filterDemoHistory);
  const map = document.getElementById("demo-map-content");
  const maps = demo.maps || [];
  if (map && maps.length) initDemoDichromatic(map, maps, projectId);
  const mapSection = document.getElementById("demo-map");
  if (mapSection) mapSection.hidden = !maps.length;
  const mapNavigation = document.querySelector('#suggestions [data-prompt="map"]');
  if (mapNavigation) mapNavigation.hidden = !maps.length;
  document.getElementById("demo-transcript-download")?.remove();
  if (Object.hasOwn(demo.files || {}, "chat/transcript.md")) {
    const transcript = document.createElement("a");
    transcript.id = "demo-transcript-download";
    transcript.href = projectFileUrl(projectId, "chat/transcript.md");
    transcript.download = `${projectId}-transcript.md`;
    transcript.textContent = "Download full transcript";
    document.getElementById("demo-history")?.appendChild(transcript);
  }
}
