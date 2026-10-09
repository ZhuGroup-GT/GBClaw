import { CONFIG } from "./config.js";
import { loadDemo, projectFileUrl } from "./demo_api.js";
import { renderMarkdown } from "./markdown.js?v=5";
import { enhanceArtifactImages } from "./artifact_images.js?v=4";
import { openImageModal } from "./image_modal.js?v=1";
import { initDemoDichromatic } from "./demo_dichromatic.js";

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
  enhanceArtifactImages(container, { projectId: CONFIG.demoProjectId, append: false,
    onOpenImage: (url, name) => openImageModal(url, name) });
  const source = document.createElement("a");
  source.href = projectFileUrl(CONFIG.demoProjectId, "memory/summary.md");
  source.download = "DemoProj-summary.md";
  source.textContent = "Download the recorded project summary";
  container.appendChild(source);
}

function renderGallery(demo) {
  const container = document.getElementById("demo-gallery-content");
  if (!container) return;
  container.replaceChildren();
  for (const result of demo.gallery || []) {
    const card = document.createElement("article");
    card.className = "demo-gallery-card";
    const imageButton = document.createElement("button");
    imageButton.type = "button";
    imageButton.className = "demo-gallery-image";
    imageButton.setAttribute("aria-label", `View ${result.title}`);
    const img = document.createElement("img");
    img.src = projectFileUrl(CONFIG.demoProjectId, result.path);
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
    description.textContent = result.description || result.workflow.replaceAll("_", " ");
    const source = document.createElement("a");
    source.href = projectFileUrl(CONFIG.demoProjectId, result.path);
    source.download = result.path.split("/").pop();
    source.textContent = "Download image";
    caption.append(title, description, source);
    if (result.manifest_path) {
      const manifest = document.createElement("a");
      manifest.href = projectFileUrl(CONFIG.demoProjectId, result.manifest_path);
      manifest.target = "_blank";
      manifest.rel = "noopener";
      manifest.textContent = "Run manifest";
      caption.appendChild(manifest);
    }
    card.append(imageButton, caption);
    container.appendChild(card);
  }
}

export async function initDemoShowcase() {
  const demo = await loadDemo();
  renderStats(demo);
  renderMemory(demo);
  renderGallery(demo);
  document.getElementById("demo-history-search")?.addEventListener("input", filterDemoHistory);
  const map = document.getElementById("demo-map-content");
  if (map) initDemoDichromatic(map, demo.maps || []);
  const transcript = document.createElement("a");
  transcript.href = projectFileUrl(CONFIG.demoProjectId, "chat/transcript.md");
  transcript.download = "DemoProj-transcript.md";
  transcript.textContent = "Download full transcript";
  document.getElementById("demo-history")?.appendChild(transcript);
}
