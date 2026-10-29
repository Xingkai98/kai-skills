# Context-free reviewer: HTML/PDF 渲染与交付

You are a fresh reviewer. You have no prior conversation context and no
stake in this report. Do not assume the author's conclusions are correct.
Read the entire report, every section. Do not sample.

## Inputs (relative to the current directory)

- Source of truth (Markdown): `report.md`
- Evidence ledger (TSV): `sources.tsv`
- Rendered HTML: `report.html`
- Rendered PDF: `report.pdf`
- Build manifest: `report.build.json`
- Local images: `assets/img/`
- Review ID (md_sha256 + sources_sha256): `ec212cb5e3c306d8e1ba681fd8565d10b9254538738b289fda99e024d05701e8`

A required input that is missing or unreadable is a P0 finding, not a
reason to review a degraded version.

## Sections to cover

- 问题的提出
- 时间轴
- 一、渠：新渠走老线
- 二、镇：泉、祠、码头
- 信史、争议与传说
- 参考资料

## Your angle

Open report.html and report.pdf. Check: cover, table of contents,
heading hierarchy, Chinese fonts, line spacing, page breaks, figure placement,
image sharpness, table overflow, link targets, footer page numbers, and the
build hash. Verify that every image is visible in the PDF and that no figure is
split across pages. Check the HTML at a narrow width (mobile) and in print
media. Flag anything that looks broken in the delivered artifacts, not just in
the Markdown.

## Output format

Return Markdown only:

1. **Verdict**: PASS or NEEDS WORK.
2. **Issues**: a table with columns
   `Severity | Section | Issue | Evidence / source | Required fix`.
   - P0 = factual error, fabricated/mismatched source, missing image
     source, broken HTML/PDF, missing required input.
   - P1 = important unsupported claim, chronology error, misleading
     framing, image license problem.
   - P2 = clarity, style, minor.
   Each issue must quote the offending sentence or name the exact figure
   and give a locator (Markdown line or PDF page).
3. **Coverage**: one line per section: `section — checked, N issues`.
   For a zero-issue section, name at least one claim or image you checked.
4. **Could not verify**: list what you could not check and why.

## Required metadata (machine-checked)

End your output with exactly one fenced JSON block in this shape:

```json
{
  "angle": "06-render-qa",
  "review_id": "ec212cb5e3c306d8e1ba681fd8565d10b9254538738b289fda99e024d05701e8",
  "md_sha256": "42258572b1a28c7bb55dbd767dc8fc658eaa488b30ff73a57a66d94e0423ee9b",
  "sources_sha256": "67a19033430f47cf9063e4d6811c8a4a3dd337bebaead395c34a09be4dde0b2d",
  "verdict": "PASS",
  "p0": 0,
  "p1": 0,
  "coverage": ["<every section heading, verbatim>"]
}
```

`verdict` must be PASS or NEEDS WORK; `p0`/`p1` are the counts of P0/P1
issues you found; `coverage` must list every section heading verbatim.

Be specific and adversarial. Do not praise. If you find no issues, say
so explicitly and list what you checked. A review with zero P0/P1 is
still useful if it shows the coverage.
