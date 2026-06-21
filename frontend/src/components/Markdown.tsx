import { Fragment, type ReactNode } from "react";

/**
 * Sanitize a markdown link target. Content can be untrusted (LLM advice / legal
 * docs), so only allow http(s)/mailto schemes and relative (/-rooted) URLs.
 * Anything else (javascript:, data:, vbscript:, …) returns null → no href.
 */
function safeHref(raw: string): string | null {
  // Strip whitespace and control chars (incl. tab/newline/NUL/DEL) that could
  // hide a dangerous scheme like "java\tscript:".
  // eslint-disable-next-line no-control-regex
  const url = raw.replace(/[\u0000-\u0020\u007f]/g, "");
  if (!url) return null;
  // Relative URLs rooted at '/' (but not protocol-relative '//') are safe.
  if (url.startsWith("/") && !url.startsWith("//")) return url;
  // If there's a scheme, only permit a known-safe allowlist (case-insensitive).
  const schemeMatch = /^([a-z][a-z0-9+.-]*):/i.exec(url);
  if (schemeMatch) {
    const scheme = schemeMatch[1].toLowerCase();
    if (scheme === "http" || scheme === "https" || scheme === "mailto") {
      return url;
    }
    return null;
  }
  // No scheme and not '/'-rooted (e.g. "example.com/x", "#anchor") → treat as relative.
  return url;
}

/** Inline formatting: **bold**, _em_, [text](url). Deliberately tiny. */
function inline(text: string, keyBase: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|_(.+?)_|\[(.+?)\]\((.+?)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[1] !== undefined) {
      nodes.push(<strong key={`${keyBase}-b${i}`}>{m[1]}</strong>);
    } else if (m[2] !== undefined) {
      nodes.push(<em key={`${keyBase}-e${i}`}>{m[2]}</em>);
    } else if (m[3] !== undefined) {
      const href = safeHref(m[4]);
      if (href) {
        nodes.push(
          <a
            key={`${keyBase}-a${i}`}
            href={href}
            className="text-indigo-400 underline"
            target="_blank"
            rel="noreferrer"
          >
            {m[3]}
          </a>,
        );
      } else {
        // Unsafe/unsupported URL scheme: render the link text without a href.
        nodes.push(<Fragment key={`${keyBase}-a${i}`}>{m[3]}</Fragment>);
      }
    }
    last = re.lastIndex;
    i += 1;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/** Minimal block markdown: #/##/### headings, > quotes, - lists, paragraphs. */
export function Markdown({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  let para: string[] = [];
  let key = 0;

  const flushPara = () => {
    if (para.length) {
      blocks.push(
        <p key={`p${key++}`} className="mt-3 leading-relaxed text-muted-foreground">
          {inline(para.join(" "), `p${key}`)}
        </p>,
      );
      para = [];
    }
  };
  const flushList = () => {
    if (list.length) {
      blocks.push(
        <ul key={`ul${key++}`} className="mt-3 list-disc space-y-1 pl-5 text-muted-foreground">
          {list.map((li, idx) => (
            <li key={idx}>{inline(li, `li${key}-${idx}`)}</li>
          ))}
        </ul>,
      );
      list = [];
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      flushPara();
      flushList();
      continue;
    }
    if (line.startsWith("### ")) {
      flushPara();
      flushList();
      blocks.push(
        <h3 key={`h${key++}`} className="mt-6 text-base font-semibold">
          {inline(line.slice(4), `h${key}`)}
        </h3>,
      );
    } else if (line.startsWith("## ")) {
      flushPara();
      flushList();
      blocks.push(
        <h2 key={`h${key++}`} className="mt-7 text-lg font-semibold">
          {inline(line.slice(3), `h${key}`)}
        </h2>,
      );
    } else if (line.startsWith("# ")) {
      flushPara();
      flushList();
      blocks.push(
        <h1 key={`h${key++}`} className="text-2xl font-bold">
          {inline(line.slice(2), `h${key}`)}
        </h1>,
      );
    } else if (line.startsWith("> ")) {
      flushPara();
      flushList();
      blocks.push(
        <blockquote
          key={`q${key++}`}
          className="mt-3 border-l-2 border-indigo-500/50 bg-muted/40 py-2 pl-4 text-sm text-muted-foreground"
        >
          {inline(line.slice(2), `q${key}`)}
        </blockquote>,
      );
    } else if (/^[-*] /.test(line)) {
      flushPara();
      list.push(line.slice(2));
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();

  return <Fragment>{blocks}</Fragment>;
}
