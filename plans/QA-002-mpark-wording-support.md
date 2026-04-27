# QA-002: mpark/wg21 Framework Support and Wording Section Handling

Status: BACKLOG
Created: 2026-04-27
Owner: SG
Depends on: QA-001

## Origin

Mungo Gill (2026-04-27, Slack #cppa-wg21):

> "You should also be aware of the mpark https://github.com/mpark/wg21
> framework. We don't use it ourselves (it is a bit inflexible and the pdf
> files are not notably attractive) but many in WG21 do, particularly for
> papers that include wording sections. We absolutely need to be able to
> parse the pdf files it generates (and turn it into our own markdown
> conventions)."

> "(note: not every paper uses strikeout font for removals - they just use
> red and green text)"

## What is mpark/wg21

- Repository: https://github.com/mpark/wg21 (146 stars)
- Blog post: https://mpark.github.io/programming/2018/11/16/how-i-format-my-cpp-papers/
- Example papers: https://github.com/mpark/wg21-papers
- Used by: Eric Niebler (https://github.com/ericniebler/wg21-mpark),
  Mateusz Pusz (https://github.com/mpusz/wg21-papers), many others

A Pandoc-based framework for writing WG21 papers in Markdown, generating
HTML or PDF output. Widely used across the committee.

## mpark-specific elements that tomd must handle

### Comparison Tables (:::cmptable)

mpark uses `::: cmptable` fenced divs for side-by-side "before/after"
comparison tables (aka "Tony Tables"). These render as two-column tables
in the PDF. tomd must recognize this layout in PDFs and convert it to
appropriate markdown tables.

### Embedded Markdown within Code (@...@)

Within code blocks, text surrounded by `@` is rendered as markdown
(e.g. `@unspecified@` becomes italicized `unspecified`). In the PDF
output, these appear as styled text within code blocks.

### Proposed Wording

mpark has its own proposed wording support with grammar changes.
The PDF rendering uses:
- Red text for deletions (not always strikethrough)
- Green text for additions (not always underline)
- Specific indentation and formatting for standard references

### YAML-based References (CSL format)

Unlike Vinnie's manual `[N] [Link](url) - "Title"` format, mpark uses
Pandoc's CSL citation system with YAML metadata blocks for references.
The PDF output renders these differently.

### Unicode in LaTeX

mpark documents known issues with Unicode characters in LaTeX-generated
PDFs. Papers using xelatex may have different font rendering that affects
tomd extraction.

## Scope

This plan covers two areas:

### Area 1: PDF Parsing (tomd converter)

Ensure tomd's PDF extraction pipeline correctly handles PDFs generated
by the mpark framework:

- Detect red/green colored text as wording additions/deletions
- Detect comparison table layouts
- Handle mpark-specific page layouts and margins
- Convert to cppalliance markdown conventions (:::wording divs, <ins>/<del>)

### Area 2: QA Scoring (qa.py)

After QA-001 is complete, extend scoring to validate:
- Wording section detection (:::wording divs present in ask-papers)
- Correct <ins>/<del> markup structure
- Comparison table integrity

## Reference Papers for Testing

Need to identify specific mpark-generated PDFs with wording sections.
Candidates:
- Papers from https://github.com/mpark/wg21-papers
- Papers from https://github.com/mpusz/wg21-papers
- Ask Mungo for a specific recommendation

## Key Links

| Resource | URL |
|---|---|
| mpark/wg21 framework | https://github.com/mpark/wg21 |
| mpark blog post | https://mpark.github.io/programming/2018/11/16/how-i-format-my-cpp-papers/ |
| mpark example papers | https://github.com/mpark/wg21-papers |
| Niebler fork | https://github.com/ericniebler/wg21-mpark |
| Pusz papers | https://github.com/mpusz/wg21-papers |
| cppalliance style rules | https://github.com/cppalliance/wg21-papers/blob/master/source/CLAUDE.md |
| cppalliance wording example | https://github.com/cppalliance/wg21-papers/blob/master/archive/p2583r3-symmetric-transfer.md |

## Next Steps

1. Complete QA-001 first (mojibake + heading skips)
2. Ask Mungo for a specific mpark-generated PDF test case
3. Download and analyze mpark PDF layout vs. cppalliance PDF layout
4. Identify differences in tomd extraction pipeline
5. Implement converter changes
6. Extend QA scoring
