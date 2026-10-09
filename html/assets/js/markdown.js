const { marked, DOMPurify, katex, hljs } = globalThis;

if (!marked?.parse) {
  throw new Error("marked is not loaded — check script tags in index.html");
}
if (!DOMPurify?.sanitize) {
  throw new Error("DOMPurify is not loaded — check script tags in index.html");
}
if (!katex?.renderToString) {
  console.warn("KaTeX is not loaded — math rendering disabled");
}

marked.use({
  gfm: true,
  breaks: true,
  renderer: {
    code({ text, lang }) {
      if (hljs?.highlight) {
        if (lang && hljs.getLanguage(lang)) {
          const highlighted = hljs.highlight(text, { language: lang }).value;
          return `<pre><code class="hljs language-${lang}">${highlighted}</code></pre>`;
        }
        const highlighted = hljs.highlightAuto(text).value;
        return `<pre><code class="hljs">${highlighted}</code></pre>`;
      }
      const escaped = text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
      return `<pre><code>${escaped}</code></pre>`;
    },
  },
});

const MATH_PLACEHOLDER = (id) => `<span data-math-id="${id}" class="math-slot"></span>`;
const STASH_TOKEN = (id) => `\uE000${id}\uE001`;
const COMBINING_BAR = /[\u0304\u0305]/;
const COMBINING_BARS = /[\u0304\u0305]/g;

function millerAtoms(group) {
  const atoms = [];
  for (let index = 0; index < group.length; index += 1) {
    const character = group[index];
    if (!/[0-9]/.test(character)) return null;
    if (COMBINING_BAR.test(group[index + 1] || "")) {
      atoms.push(character + group[index + 1]);
      index += 1;
    } else {
      atoms.push(character);
    }
  }
  return atoms;
}

function legacyMillerComponents(body) {
  const groups = body.trim().split(/\s+/).filter(Boolean);
  if (!groups.length || groups.length > 3) return null;

  const atomsByGroup = groups.map(millerAtoms);
  if (atomsByGroup.some((atoms) => !atoms)) return null;

  if (groups.length === 3) return groups;

  if (groups.length === 1) {
    const atoms = atomsByGroup[0];
    if (atoms.length === 3) return atoms;

    // Legacy compact output marks a negative one-digit component by placing
    // the combining bar on that digit. With four or more digits, that marked
    // digit separates the first two components from a multi-digit third one:
    // `11̄24` => 1, -1, 24 and `77̄12` => 7, -7, 12.
    const markedIndex = atoms.findIndex((atom, index) => (
      index > 0 && index < atoms.length - 1 && COMBINING_BAR.test(atom)
    ));
    if (markedIndex !== -1) {
      const components = [
        atoms.slice(0, markedIndex).join(""),
        atoms[markedIndex],
        atoms.slice(markedIndex + 1).join(""),
      ];
      const absolute = components.map((part) => part.replace(COMBINING_BARS, ""));
      if (absolute[0] === absolute[1]) return components;
    }
    return null;
  }

  // Two whitespace groups encode three components when two compact
  // single-digit components precede a multi-digit one, e.g.
  // `1̄1 19̄` => -1, 1, -19.
  for (let groupIndex = 0; groupIndex < atomsByGroup.length; groupIndex += 1) {
    const atoms = atomsByGroup[groupIndex];
    const markedIndex = atoms.findIndex((atom, index) => (
      index < atoms.length - 1 && COMBINING_BAR.test(atom)
    ));
    if (markedIndex === -1) continue;

    const split = [
      atoms.slice(0, markedIndex + 1).join(""),
      atoms.slice(markedIndex + 1).join(""),
    ];
    const components = groupIndex === 0
      ? [...split, groups[1]]
      : [groups[0], ...split];
    const absolute = components.map((part) => part.replace(COMBINING_BARS, ""));
    if (absolute[0] === absolute[1]) return components;
  }

  return null;
}

function structuredMillerNotationToTex(value) {
  const delimiters = {
    "(": [")", "(", ")"],
    "[": ["]", "[", "]"],
    "{": ["}", "\\{", "\\}"],
    "<": [">", "\\langle ", "\\rangle"],
  };
  const opening = value[0];
  const delimiter = delimiters[opening];
  if (!delimiter || value.at(-1) !== delimiter[0]) return null;

  const components = legacyMillerComponents(value.slice(1, -1));
  if (!components || components.length !== 3) return null;

  const rendered = components.map((component) => {
    const negative = COMBINING_BAR.test(component);
    const digits = component.replace(COMBINING_BARS, "");
    if (!/^\d+$/.test(digits)) return null;
    if (!negative) return digits;
    return digits.length === 1 ? `\\bar{${digits}}` : `\\overline{${digits}}`;
  });
  if (rendered.some((component) => !component)) return null;

  return `${delimiter[1]}${rendered.join("\\,")}${delimiter[2]}`;
}

function millerNotationToTex(value) {
  const structured = structuredMillerNotationToTex(value);
  if (structured) return structured;

  let tex = "";

  for (let index = 0; index < value.length; index += 1) {
    const character = value[index];
    const next = value[index + 1];

    if (/[0-9A-Za-z]/.test(character) && (next === "\u0304" || next === "\u0305")) {
      tex += `\\bar{${character}}`;
      index += 1;
      continue;
    }

    if (/\s/.test(character)) {
      // TeX ignores literal whitespace in math mode. Preserve one visible
      // separator between multi-digit Miller components, without allowing
      // alignment whitespace from model output to accumulate.
      tex += "\\;";
      while (/\s/.test(value[index + 1] || "")) index += 1;
    }
    else if (character === "−") tex += "-";
    else if (character === "{") tex += "\\{";
    else if (character === "}") tex += "\\}";
    else if (character === "<") tex += "\\langle ";
    else if (character === ">") tex += "\\rangle ";
    else if (character === "|") tex += "\\mid ";
    else tex += character;
  }

  return tex;
}

function protectMillerNotation(text, mathSlots) {
  // Keep fenced code literal. Convert compact crystallographic notation in
  // inline-code spans so legacy Unicode combining bars are typeset by KaTeX.
  return text.replace(/```[\s\S]*?```|`([^`\n]+)`/g, (match, inlineCode) => {
    if (match.startsWith("```") || !/[\u0304\u0305]/.test(inlineCode || "")) {
      return match;
    }

    const value = inlineCode.trim();
    if (!/^[0-9+\-−()\[\]{}<>,;|.\s\u0304\u0305]+$/.test(value)) {
      return match;
    }

    const id = mathSlots.length;
    mathSlots.push({ type: "inline", tex: millerNotationToTex(value) });
    return MATH_PLACEHOLDER(id);
  });
}

function stashSegments(text, segments) {
  let result = text;
  result = result.replace(/```[\s\S]*?```/g, (match) => {
    const id = segments.length;
    segments.push(match);
    return STASH_TOKEN(id);
  });
  result = result.replace(/`[^`\n]+`/g, (match) => {
    const id = segments.length;
    segments.push(match);
    return STASH_TOKEN(id);
  });
  return result;
}

function restoreSegments(html, segments) {
  return html.replace(/\uE000(\d+)\uE001/g, (_, id) => {
    const chunk = segments[Number(id)];
    if (!chunk) return "";
    if (chunk.startsWith("```")) return marked.parse(chunk);
    return marked.parseInline(chunk);
  });
}

function protectMath(text, mathSlots) {
  let protectedText = text;

  protectedText = protectedText.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => {
    const id = mathSlots.length;
    mathSlots.push({ type: "block", tex: tex.trim() });
    return MATH_PLACEHOLDER(id);
  });

  protectedText = protectedText.replace(/\\\[([\s\S]+?)\\\]/g, (_, tex) => {
    const id = mathSlots.length;
    mathSlots.push({ type: "block", tex: tex.trim() });
    return MATH_PLACEHOLDER(id);
  });

  protectedText = protectedText.replace(/(?<!\$)\$(?!\$)((?:\\.|[^$\\])+?)\$(?!\$)/g, (_, tex) => {
    const id = mathSlots.length;
    mathSlots.push({ type: "inline", tex: tex.trim() });
    return MATH_PLACEHOLDER(id);
  });

  protectedText = protectedText.replace(/\\\((.+?)\\\)/g, (_, tex) => {
    const id = mathSlots.length;
    mathSlots.push({ type: "inline", tex: tex.trim() });
    return MATH_PLACEHOLDER(id);
  });

  return protectedText;
}

function renderMath(tex, displayMode) {
  if (!katex?.renderToString) {
    return displayMode ? `$$${tex}$$` : `$${tex}$`;
  }
  try {
    return katex.renderToString(tex, {
      displayMode,
      throwOnError: false,
      strict: "ignore",
    });
  } catch {
    return displayMode ? `$$${tex}$$` : `$${tex}$`;
  }
}

function restoreMath(html, mathSlots) {
  return html.replace(/<span data-math-id="(\d+)" class="math-slot"><\/span>/g, (_, id) => {
    const slot = mathSlots[Number(id)];
    if (!slot) return "";
    return renderMath(slot.tex, slot.type === "block");
  });
}

function wrapMarkdownTables(html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  for (const table of template.content.querySelectorAll("table")) {
    if (table.parentElement?.classList.contains("markdown-table-scroll")) continue;
    const wrapper = document.createElement("div");
    wrapper.className = "markdown-table-scroll";
    table.before(wrapper);
    wrapper.appendChild(table);
  }
  return template.innerHTML;
}

/**
 * Render markdown for a still-growing stream (closes open code fences).
 */
export function renderMarkdownStreaming(text) {
  if (!text) return "";

  let source = String(text);
  const fenceCount = (source.match(/```/g) || []).length;
  if (fenceCount % 2 === 1) {
    source += "\n```";
  }

  return renderMarkdown(source);
}

/**
 * Render model markdown (tables, code, LaTeX) to safe HTML.
 */
export function renderMarkdown(text) {
  if (!text) return "";

  const segments = [];
  const mathSlots = [];
  const withMillerMath = protectMillerNotation(String(text), mathSlots);
  const stashed = stashSegments(withMillerMath, segments);
  const withMath = protectMath(stashed, mathSlots);
  const withSegments = restoreSegments(marked.parse(withMath), segments);
  const sanitized = DOMPurify.sanitize(withSegments, {
    USE_PROFILES: { html: true },
    ADD_ATTR: ["data-math-id"],
  });
  return wrapMarkdownTables(restoreMath(sanitized, mathSlots));
}
