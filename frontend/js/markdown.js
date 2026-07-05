// Minimal, dependency-free and XSS-safe Markdown renderer.
//
// Everything is HTML-escaped first; only a known-safe subset of tags is then
// introduced. Link URLs are restricted to safe schemes. No external library is
// needed (works fully offline).

import { renderCodeBlock } from "./highlight.js";

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function safeUrl(url) {
  const u = String(url).trim();
  if (/^(https?:|mailto:|#|\/)/i.test(u)) return u;
  return "#";
}

// Inline formatting on already-escaped text.
function inline(text) {
  let t = text;
  t = t.replace(/`([^`]+)`/g, (_, code) => `<code>${code}</code>`);
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
  t = t.replace(/(^|[^_])_([^_\s][^_]*)_/g, "$1<em>$2</em>");
  t = t.replace(
    /\[([^\]]+)\]\(([^)]+)\)/g,
    (_, label, url) =>
      `<a href="${safeUrl(url)}" target="_blank" rel="noopener noreferrer">${label}</a>`
  );
  return t;
}

export function renderMarkdown(src) {
  const rawLines = (src || "").split(/\r?\n/);
  const lines = rawLines.map(escapeHtml);
  const out = [];
  let paragraph = [];
  let listType = null; // 'ul' | 'ol'

  const flushParagraph = () => {
    if (paragraph.length) {
      out.push(`<p>${inline(paragraph.join(" "))}</p>`);
      paragraph = [];
    }
  };
  const closeList = () => {
    if (listType) {
      out.push(`</${listType}>`);
      listType = null;
    }
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block
    if (/^```/.test(line)) {
      flushParagraph();
      closeList();
      const lang = (line.match(/^```\s*([\w+-]+)/) || [])[1] || "";
      const buffer = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) {
        buffer.push(rawLines[i]); // raw source; the highlighter escapes it
        i++;
      }
      i++; // skip closing fence
      out.push(renderCodeBlock(buffer.join("\n"), lang));
      continue;
    }

    // Heading
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushParagraph();
      closeList();
      const level = heading[1].length;
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      i++;
      continue;
    }

    // Horizontal rule
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      flushParagraph();
      closeList();
      out.push("<hr />");
      i++;
      continue;
    }

    // Blockquote
    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      closeList();
      out.push(`<blockquote>${inline(quote[1])}</blockquote>`);
      i++;
      continue;
    }

    // Unordered list
    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    if (ul) {
      flushParagraph();
      if (listType !== "ul") {
        closeList();
        out.push("<ul>");
        listType = "ul";
      }
      out.push(`<li>${inline(ul[1])}</li>`);
      i++;
      continue;
    }

    // Ordered list
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ol) {
      flushParagraph();
      if (listType !== "ol") {
        closeList();
        out.push("<ol>");
        listType = "ol";
      }
      out.push(`<li>${inline(ol[1])}</li>`);
      i++;
      continue;
    }

    // Blank line
    if (/^\s*$/.test(line)) {
      flushParagraph();
      closeList();
      i++;
      continue;
    }

    // Paragraph text
    closeList();
    paragraph.push(line);
    i++;
  }

  flushParagraph();
  closeList();
  return out.join("\n");
}
