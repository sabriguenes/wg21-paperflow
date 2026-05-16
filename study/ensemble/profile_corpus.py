"""Quick profiler: identify which sid ranges are wording-style vs prose."""
import json
import re
from pathlib import Path

rows = json.loads(
    Path("study/ensemble/data/p2300r10_sentences.json").read_text(encoding="utf-8")
)
wording_re = re.compile(
    r"\*(Effects|Mandates|Preconditions|Constraints|Throws|Remarks|Returns|"
    r"Hypothetically|Recommended|Type)\:\*"
)
ins_del_re = re.compile(r"<(ins|del)>")
let_denote_re = re.compile(
    r"(Let\s+|denote|expression-equivalent|exposition-only|equivalent to:)",
    re.IGNORECASE,
)
ranges = [
    ("0-300 (examples,phase1)",  (0, 300)),
    ("300-700",                   (300, 700)),
    ("700-1100",                  (700, 1100)),
    ("1100-1500",                 (1100, 1500)),
    ("1500-2000",                 (1500, 2000)),
    ("2000-2500",                 (2000, 2500)),
    ("2500-2797",                 (2500, 2797)),
]
print(f'{"range":30}  {"n":>4}  {"wording":>7}  {"ins/del":>7}  {"let/eqv":>7}')
for label, (lo, hi) in ranges:
    subset = [r for r in rows if lo <= r["sid"] < hi]
    wording = sum(1 for r in subset if wording_re.search(r["text"]))
    insdel = sum(1 for r in subset if ins_del_re.search(r["text"]))
    let = sum(1 for r in subset if let_denote_re.search(r["text"]))
    print(f"{label:30}  {len(subset):4d}  {wording:>7d}  {insdel:>7d}  {let:>7d}")
