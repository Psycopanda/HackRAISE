// Minimal, dependency-free and XSS-safe syntax highlighter.
//
// It tokenises the RAW source and HTML-escapes every emitted piece, so the
// result is always safe to inject. Tokens are wrapped in <span class="tok-...">
// and themed in CSS. No external library is needed (works fully offline),
// matching the philosophy of markdown.js.

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

const PY_KEYWORDS = new Set([
  "def", "class", "return", "if", "elif", "else", "for", "while", "import",
  "from", "as", "with", "try", "except", "finally", "raise", "in", "not",
  "and", "or", "is", "lambda", "yield", "global", "nonlocal", "pass", "break",
  "continue", "async", "await", "del", "assert", "match", "case",
]);
const PY_BUILTINS = new Set([
  "None", "True", "False", "self", "cls", "print", "len", "range", "int",
  "str", "float", "bool", "list", "dict", "set", "tuple", "open", "super",
  "isinstance", "enumerate", "zip", "map", "filter", "sorted", "sum", "min",
  "max", "abs", "type", "object", "Exception", "__init__",
]);

const JS_KEYWORDS = new Set([
  "const", "let", "var", "function", "return", "if", "else", "for", "while",
  "class", "extends", "new", "import", "export", "from", "as", "default",
  "async", "await", "try", "catch", "finally", "throw", "typeof",
  "instanceof", "in", "of", "switch", "case", "break", "continue", "do",
  "yield", "delete", "void", "static", "get", "set",
]);
const JS_BUILTINS = new Set([
  "this", "super", "null", "true", "false", "undefined", "NaN", "Infinity",
  "console", "document", "window", "Math", "JSON", "Object", "Array",
  "String", "Number", "Boolean", "Promise", "Map", "Set", "Symbol",
]);

const EMPTY = new Set();

// Each rule pairs a *sticky* regex (matches only at lastIndex) with a token
// class. Order matters: comments and strings must come before numbers/words.
const RULES = {
  python: [
    { cls: "comment", re: /#[^\n]*/y },
    {
      cls: "string",
      re: /(?:[rbfuRBFU]{0,2})(?:"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')/y,
    },
    { cls: "decorator", re: /@[A-Za-z_][\w.]*/y },
    { cls: "number", re: /\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?j?/y },
  ],
  js: [
    { cls: "comment", re: /\/\/[^\n]*/y },
    { cls: "comment", re: /\/\*[\s\S]*?\*\//y },
    {
      cls: "string",
      re: /(?:"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)/y,
    },
    { cls: "number", re: /\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?n?/y },
  ],
  generic: [
    { cls: "comment", re: /(?:#[^\n]*|\/\/[^\n]*|\/\*[\s\S]*?\*\/)/y },
    {
      cls: "string",
      re: /(?:"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)/y,
    },
    { cls: "number", re: /\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?/y },
  ],
};

const WORD_RE = /[A-Za-z_$][\w$]*/y;

function pickKind(language) {
  const raw = String(language || "").toLowerCase().trim();
  const value = raw.includes(".") ? raw.slice(raw.lastIndexOf(".") + 1) : raw;
  if (value === "python" || value === "py") return "python";
  if (["js", "javascript", "jsx", "mjs", "cjs", "node", "ts", "typescript", "tsx"].includes(value)) {
    return "js";
  }
  return "generic";
}

function scan(code, rules, keywords, builtins) {
  const out = [];
  let i = 0;
  const n = code.length;

  while (i < n) {
    let matched = false;
    for (const rule of rules) {
      rule.re.lastIndex = i;
      const m = rule.re.exec(code);
      if (m) {
        out.push(`<span class="tok-${rule.cls}">${escapeHtml(m[0])}</span>`);
        i = rule.re.lastIndex;
        matched = true;
        break;
      }
    }
    if (matched) continue;

    WORD_RE.lastIndex = i;
    const wm = WORD_RE.exec(code);
    if (wm) {
      const word = wm[0];
      let cls = null;
      if (keywords.has(word)) cls = "keyword";
      else if (builtins.has(word)) cls = "builtin";
      else {
        let j = WORD_RE.lastIndex;
        while (j < n && (code[j] === " " || code[j] === "\t")) j++;
        if (code[j] === "(") cls = "function";
      }
      out.push(cls ? `<span class="tok-${cls}">${escapeHtml(word)}</span>` : escapeHtml(word));
      i = WORD_RE.lastIndex;
      continue;
    }

    out.push(escapeHtml(code[i]));
    i++;
  }
  return out.join("");
}

/**
 * Highlight source code and return safe HTML (tokens wrapped in spans).
 * @param {string} code
 * @param {string} language  a language id ("python") or a file name ("app.py").
 */
export function highlightCode(code, language) {
  const src = code == null ? "" : String(code);
  // Guard against pathological cost on very large buffers.
  if (src.length > 60000) return escapeHtml(src);
  const kind = pickKind(language);
  const rules = RULES[kind];
  const keywords = kind === "python" ? PY_KEYWORDS : kind === "js" ? JS_KEYWORDS : EMPTY;
  const builtins = kind === "python" ? PY_BUILTINS : kind === "js" ? JS_BUILTINS : EMPTY;
  return scan(src, rules, keywords, builtins);
}

/** Wrap highlighted code in a <pre><code> block ready to inject. */
export function renderCodeBlock(code, language) {
  return `<pre class="codeview"><code class="codeview__code">${highlightCode(code, language)}</code></pre>`;
}
