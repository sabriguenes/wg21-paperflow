# Golden files

Expected outputs for [`test_html_golden.py`](../../test_html_golden.py) and
[`test_pdf_golden.py`](../../test_pdf_golden.py).

HTML inputs live under [`papers/`](../../../papers/) as `{stem}.html` (not
copied into this folder). PDF inputs are self-contained: `{stem}.pdf` files
are committed directly in this folder alongside their `{stem}.golden.md`.

## PDF golden files

Selected for structural diversity:

| Paper | Features |
|-------|----------|
| p0533r9 | tables, code blocks, bold, italic |
| p0957r8 | tables, 66 code blocks, 22 lists, uncertain regions |
| p1068r11 | heavy wording (94 ins), lists, code |
| p3556r0 | both ins and del wording, headings, code, links |
| p1122r3 | list-heavy (29 lists), headings, code, links |
| p2040r0 | balanced (code, lists, headings), full 5-field front matter |
| p3714r0 | minimal paper (2 headings, 1 code block) |
| p1112r4 | uncertain regions, lists, italic |

## Refreshing HTML baselines

From the `tomd/` directory, after intentionally changing HTML converter output:

```bash
python -c "
import json
from pathlib import Path
from tomd.lib.html import convert_html
ROOT = Path('.').resolve()
papers, out = ROOT / 'papers', ROOT / 'tests/fixtures/golden'
for name in ['p3411r5.html', 'p2728r11.html', 'p3953r0.html', 'p4005r0.html',
             'p4020r0.html', 'p3911r2.html', 'n5034.html']:
    stem = Path(name).stem
    md, prompts = convert_html(papers / name)
    (out / f'{stem}.golden.md').write_text(md, encoding='utf-8', newline='\n')
    ppath = out / f'{stem}.golden.prompts.json'
    if prompts:
        ppath.write_text(json.dumps(prompts, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    elif ppath.exists():
        ppath.unlink()
"
```

## Refreshing PDF baselines

From the `tomd/` directory, after intentionally changing PDF converter output:

```bash
python -c "
import json
from pathlib import Path
from tomd.lib.pdf import run_pipeline
out = Path('tests/fixtures/golden')
for stem in ['p0533r9', 'p0957r8', 'p1068r11', 'p3556r0',
             'p1122r3', 'p2040r0', 'p3714r0', 'p1112r4']:
    r = run_pipeline(out / f'{stem}.pdf')
    md, prompts = r.md, r.prompts
    (out / f'{stem}.golden.md').write_text(md, encoding='utf-8', newline='\n')
    ppath = out / f'{stem}.golden.prompts.json'
    if prompts:
        ppath.write_text(json.dumps(prompts, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    elif ppath.exists():
        ppath.unlink()
"
```

Prompts goldens are JSON arrays where each element is a self-contained LLM
reconcile prompt. Review diffs, then commit.
