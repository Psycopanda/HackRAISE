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

const BASH_KEYWORDS = new Set([
  "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done",
  "case", "esac", "function", "return", "in", "select", "time",
]);
const BASH_BUILTINS = new Set([
  "echo", "export", "cd", "pwd", "source", "alias", "unset", "read", "exit",
  "set", "shift", "trap", "local", "declare", "printf", "test", "sudo",
  "grep", "sed", "awk", "curl", "wget", "chmod", "chown", "mkdir", "rm", "cp",
  "mv", "ls", "cat", "npm", "npx", "uv", "pip", "python", "git", "docker",
]);

// SQL keywords are matched case-insensitively, so the set is lowercase.
const SQL_KEYWORDS = new Set([
  "select", "from", "where", "insert", "into", "values", "update", "set",
  "delete", "create", "table", "drop", "alter", "join", "inner", "left",
  "right", "outer", "on", "and", "or", "not", "null", "as", "group", "by",
  "order", "having", "limit", "offset", "distinct", "union", "index",
  "primary", "key", "foreign", "references", "default", "exists", "in",
  "like", "between", "case", "when", "then", "end", "asc", "desc",
]);
const SQL_BUILTINS = new Set([
  "count", "sum", "avg", "min", "max", "coalesce", "now",
]);

const CLIKE_KEYWORDS = new Set([
  "class", "public", "private", "protected", "static", "void", "int",
  "float", "double", "char", "bool", "boolean", "struct", "enum",
  "interface", "extends", "implements", "namespace", "using", "include",
  "define", "func", "fn", "return", "if", "else", "for", "while", "switch",
  "case", "break", "continue", "new", "delete", "const", "var", "let",
  "package", "import", "throws", "throw", "try", "catch", "finally", "final",
  "abstract", "override", "virtual", "template", "typename", "mut", "impl",
  "trait", "pub", "unsafe",
]);
const CLIKE_BUILTINS = new Set([
  "this", "self", "super", "true", "false", "null", "nullptr", "None",
  "System", "String", "std",
]);

const JSON_KEYWORDS = new Set(["true", "false", "null"]);

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
  bash: [
    { cls: "comment", re: /#[^\n]*/y },
    { cls: "string", re: /(?:"(?:\\.|[^"\\\n])*"|'[^'\n]*')/y },
    { cls: "decorator", re: /\$\{?\w+\}?/y },
    { cls: "number", re: /\d[\d_]*/y },
  ],
  sql: [
    { cls: "comment", re: /(?:--[^\n]*|\/\*[\s\S]*?\*\/)/y },
    { cls: "string", re: /'(?:''|[^'])*'/y },
    { cls: "number", re: /\d[\d_]*(?:\.\d+)?/y },
  ],
  clike: [
    { cls: "comment", re: /(?:\/\/[^\n]*|\/\*[\s\S]*?\*\/)/y },
    {
      cls: "string",
      re: /(?:"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')/y,
    },
    { cls: "decorator", re: /@[A-Za-z_][\w.]*/y },
    { cls: "number", re: /\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?[fFlLuU]*/y },
  ],
  json: [
    { cls: "string", re: /"(?:\\.|[^"\\\n])*"/y },
    { cls: "number", re: /-?\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?/y },
  ],
  css: [
    { cls: "comment", re: /\/\*[\s\S]*?\*\//y },
    { cls: "string", re: /(?:"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')/y },
    { cls: "decorator", re: /@[A-Za-z-]+/y },
    { cls: "number", re: /#[0-9a-fA-F]{3,8}\b|\d[\d.]*(?:%|[a-z]{1,4})?/y },
  ],
  html: [
    { cls: "comment", re: /<!--[\s\S]*?-->/y },
    { cls: "string", re: /(?:"[^"\n]*"|'[^'\n]*')/y },
    { cls: "tag", re: /<\/?[A-Za-z][\w:-]*/y },
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
  // Explicit "no highlighting" markers: respect them instead of autodetecting.
  if (["text", "txt", "plain", "plaintext", "none"].includes(value)) return "generic";
  if (value === "python" || value === "py") return "python";
  if (["js", "javascript", "jsx", "mjs", "cjs", "node", "ts", "typescript", "tsx"].includes(value)) {
    return "js";
  }
  if (["sh", "shell", "bash", "zsh", "console", "terminal"].includes(value)) return "bash";
  if (["sql", "mysql", "postgresql", "plpgsql", "sqlite"].includes(value)) return "sql";
  if (["json", "json5", "jsonc"].includes(value)) return "json";
  if (["css", "scss", "sass", "less"].includes(value)) return "css";
  if (["html", "htm", "xml", "svg", "vue"].includes(value)) return "html";
  if ([
    "java", "c", "cpp", "cxx", "cc", "h", "hpp", "csharp", "cs", "go",
    "golang", "rust", "rs", "kotlin", "kt", "swift", "php", "dart",
  ].includes(value)) {
    return "clike";
  }
  return null;
}

// Best-effort language guess from the raw code, used when no (or an
// unrecognised) fence language is given. Cheap regex heuristics, not a real
// parser — order matters, most specific/reliable checks come first.
function detectLanguage(code) {
  const src = code.trim();
  if (!src) return "generic";

  if (/^[[{]/.test(src)) {
    try {
      JSON.parse(src);
      return "json";
    } catch (_) {
      /* not JSON */
    }
  }
  if (/^<!doctype html/i.test(src) || /<\/?[a-z][\w-]*(\s[^>]*)?>/i.test(src)) {
    return "html";
  }
  if (/^#!.*\b(bash|sh|zsh)\b/.test(src) || /^\s*(sudo|echo|export|cd|curl|chmod|apt-get)\s/m.test(src)) {
    return "bash";
  }
  if (/\b(select\s+.+\s+from|insert\s+into|update\s+\w+\s+set|create\s+table|delete\s+from)\b/i.test(src)) {
    return "sql";
  }
  if (/[.#]?[\w-]+(?:\s*,\s*[.#]?[\w-]+)*\s*\{[^{}]*:[^{}]*;[^{}]*\}/.test(src) && !/=>|function\s|def\s/.test(src)) {
    return "css";
  }
  if (/\bdef\s+\w+\s*\(.*\)\s*:|^\s*(import|from)\s+\w|:\s*$/m.test(src)) {
    return "python";
  }
  if (/\b(function\s*\w*\s*\(|const\s+\w+\s*=|let\s+\w+\s*=|=>|console\.log)\b/.test(src)) {
    return "js";
  }
  if (/#include\s*[<"]|public\s+(class|static)|int\s+main\s*\(|\bfn\s+\w+\s*\(|\bfunc\s+\w+\s*\(/.test(src)) {
    return "clike";
  }
  return "generic";
}

function scan(code, rules, keywords, builtins, caseInsensitive = false) {
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
      const key = caseInsensitive ? word.toLowerCase() : word;
      let cls = null;
      if (keywords.has(key)) cls = "keyword";
      else if (builtins.has(key)) cls = "builtin";
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

const KEYWORDS_BY_KIND = {
  python: PY_KEYWORDS,
  js: JS_KEYWORDS,
  bash: BASH_KEYWORDS,
  sql: SQL_KEYWORDS,
  clike: CLIKE_KEYWORDS,
  json: JSON_KEYWORDS,
};
const BUILTINS_BY_KIND = {
  python: PY_BUILTINS,
  js: JS_BUILTINS,
  bash: BASH_BUILTINS,
  sql: SQL_BUILTINS,
  clike: CLIKE_BUILTINS,
};

/**
 * Highlight source code and return safe HTML (tokens wrapped in spans).
 * @param {string} code
 * @param {string} language  a language id ("python") or a file name ("app.py").
 *   When omitted or unrecognised, the language is auto-detected from `code`.
 */
export function highlightCode(code, language) {
  const src = code == null ? "" : String(code);
  // Guard against pathological cost on very large buffers.
  if (src.length > 60000) return escapeHtml(src);
  const kind = pickKind(language) || detectLanguage(src);
  const rules = RULES[kind] || RULES.generic;
  const keywords = KEYWORDS_BY_KIND[kind] || EMPTY;
  const builtins = BUILTINS_BY_KIND[kind] || EMPTY;
  return scan(src, rules, keywords, builtins, kind === "sql");
}

/** Wrap highlighted code in a <pre><code> block ready to inject. */
export function renderCodeBlock(code, language) {
  return `<pre class="codeview"><code class="codeview__code">${highlightCode(code, language)}</code></pre>`;
}
