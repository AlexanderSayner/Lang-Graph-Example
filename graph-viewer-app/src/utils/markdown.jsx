import React from 'react';

// Minimal, SAFE markdown for chat answers.
//
// An LLM answer is untrusted text: it may contain `<script>`, `<img onerror=…>` or any other
// markup a model was talked into emitting (or that a document it summarised carried). This
// module therefore NEVER touches innerHTML and never builds a string of markup - every piece
// of formatting is a React element, so anything that is not recognised formatting survives as
// literal text and can do nothing.
//
// What it supports is deliberately small: fenced code blocks, paragraphs, `inline code`,
// **bold** and *italic*. That is what an answer actually needs; a full markdown engine would
// add a parser to the attack surface for headings nobody writes in a chat reply.
const INLINE = /(`[^`]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*)/g;
const FENCE = /```/;

/** Render `code`, **bold** and *italic* inside one paragraph. */
function inline(text, keyPrefix) {
    return String(text)
        .split(INLINE)
        .filter(part => part !== '')
        .map((part, i) => {
            const key = `${keyPrefix}-${i}`;
            if (part.length > 1 && part.startsWith('`') && part.endsWith('`')) {
                return <code key={key} className="msg-inline-code">{part.slice(1, -1)}</code>;
            }
            if (part.length > 3 && part.startsWith('**') && part.endsWith('**')) {
                return <strong key={key}>{part.slice(2, -2)}</strong>;
            }
            if (part.length > 1 && part.startsWith('*') && part.endsWith('*')) {
                return <em key={key}>{part.slice(1, -1)}</em>;
            }
            return <React.Fragment key={key}>{part}</React.Fragment>;
        });
}

/**
 * Does this line look like a markdown table separator (`| --- | :-- |`)?
 * It is what distinguishes a table from a paragraph that happens to contain pipes.
 */
function isSeparator(line) {
    return /^\s*\|?[\s:|-]+\|?\s*$/.test(line) && line.includes('-');
}

/** Render a block of pipe-delimited rows as a real <table>. */
function table(rows, keyPrefix) {
    const cells = row => row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|')
        .map(cell => cell.trim());
    const header = cells(rows[0]);
    const body = rows.slice(2).map((row, i) => cells(row));
    return (
        <div key={keyPrefix} className="msg-table-wrap">
            <table className="msg-table">
                <thead>
                    <tr>{header.map((cell, j) => <th key={j}>{inline(cell, `${keyPrefix}-h${j}`)}</th>)}</tr>
                </thead>
                <tbody>
                    {body.map((row, i) => (
                        <tr key={i}>{row.map((cell, j) => <td key={j}>{inline(cell, `${keyPrefix}-r${i}-${j}`)}</td>)}</tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

/**
 * Turn one answer into React nodes.
 *
 * ``` fences are split first, because everything inside one is code and must not be formatted:
 * a JSON answer wrapped in a fence is the single most common shape, and bolding a key inside
 * it would be actively misleading.
 */
export function renderMarkdown(text) {
    let source = String(text ?? '');
    if (!source.trim()) return null;

    // A model often wraps its WHOLE answer in one ``` fence. To the renderer that reads as
    // "one big code block", which turns a readable report into monospace text - the exact
    // complaint "the markdown came through unformatted". When the fence covers the entire
    // message (exactly two markers, one at each end), it is not a real code block: unwrap it
    // and render the inside as normal markdown.
    const trimmed = source.trim();
    const fenceCount = (source.match(/```/g) || []).length;
    if (fenceCount === 2 && trimmed.startsWith('```') && trimmed.endsWith('```')) {
        const bodyStart = trimmed.indexOf('\n') + 1;
        source = trimmed.slice(bodyStart, trimmed.lastIndexOf('```'));
    }

    const out = [];
    source.split(FENCE).forEach((block, index) => {
        if (index % 2 === 1) {
            // Odd segments are fenced. Drop the language line when one is present, so the
            // block shows the body and not the "json" label the model wrote after the fence.
            const newline = block.indexOf('\n');
            const hasLanguage = newline > 0
                && /^[ \t]*[a-zA-Z0-9_+#.-]+[ \t]*$/.test(block.slice(0, newline));
            const code = (hasLanguage ? block.slice(newline + 1) : block).replace(/\n+$/, '');
            out.push(
                <pre key={`code-${index}`} className="msg-code">
                    <code>{code}</code>
                </pre>
            );
            return;
        }
        // Even segments are prose. They may contain tables, which are separated from the
        // surrounding paragraphs by blank lines like any other block.
        block.split(/\n{2,}/).forEach((chunkIn, j) => {
            if (!chunkIn.trim()) return;
            const key = `p${index}-${j}`;
            // A model sometimes emits a whole table on ONE line. Its rows are separated by a
            // doubled pipe (a cell closing, then the next row opening: `… 8 ч | | Артамонова`),
            // while the cells inside one row are separated by a single pipe (`| Jira | Не`),
            // so the doubled pipe is the one unambiguous row boundary. Re-split on it, and the
            // normal detection below takes over - otherwise the table prints as prose.
            const chunk = !chunkIn.includes('\n') && /\|\s*-{2,}/.test(chunkIn)
                ? chunkIn.replace(/\s*\|\s*\|\s*/g, '\n')
                : chunkIn;
            const lines = chunk.split('\n');
            // A table needs a header row, a separator row, then at least one body row.
            if (lines.length >= 3 && lines[0].trim().startsWith('|') && isSeparator(lines[1])) {
                out.push(table(lines.filter(line => line.trim()), key));
                return;
            }
            out.push(
                <p key={key} className="msg-para">
                    {inline(chunk, key)}
                </p>
            );
        });
    });

    return out;
}

