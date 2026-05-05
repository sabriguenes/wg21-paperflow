# Code Quality Report: wg21-paperflow

Pattern categories for team review. No function names, no blame - just patterns to avoid going forward.

- **15 pattern categories, 49 total instances**
- **2 high severity, 6 medium, 2 notes, 5 low**
- **Overall grade: B+** - architecture is sound, testing is serious (0.8:1 test-to-source ratio), async patterns are correct. These are cleanup-sprint items, not structural problems.

---

## High Severity

### Duplicate Side Effects (1 instance)

- **What happened:** A library function persisted a result to disk, then the CLI caller persisted the same result again. The ownership of the write was ambiguous.
- **Rule:** Pick one owner for each side effect. Library functions return data; callers persist. If the library writes, the caller must not also write. Document who owns persistence in the function docstring.

### Unvalidated Lookup (1 instance)

- **What happened:** A dictionary lookup used bracket indexing on a key extracted from a config file. An unknown key would throw a raw KeyError with no context.
- **Rule:** Use .get() and raise a domain-specific error naming the bad key and the valid alternatives. Never let a raw KeyError escape a public function.

---

## Medium Severity

### Deprecated API Surface (5 instances)

- **What happened:** Deprecated shim methods lingered in an abstract base class with "use X instead" docstrings but no removal timeline. One still had a production caller.
- **Rule:** Deprecation without a removal date is debt with interest. Migrate the last caller and delete the shims. If external consumers exist, add a version gate.

### Magic Numbers as Control Flow (2 instances)

- **What happened:** Pipeline steps were gated on numeric indices (== 7, == 8) rather than the step's string key. Reordering steps would silently break the logic.
- **Rule:** Reference steps by name or key, never by position. If you must use an index, derive it from a lookup, not a literal.

### stderr Print in Library Code (1 instance)

- **What happened:** A conversion function used print(file=sys.stderr) to report uncertain regions. Callers that embed the function in a different UI cannot suppress or redirect the output.
- **Rule:** Library code uses logging, never print(). The caller configures handlers. If the function is "pure," it returns data about problems rather than printing them.

### Overly Broad Exception Handling (3 instances)

- **What happened:** except Exception caught everything and logged at debug level, making failures nearly invisible at default verbosity. Separate from batch-robustness patterns where broad catches are justified.
- **Rule:** Catch the narrowest exception type the API documents. Log at warning or higher so failures are visible. Broad catches are acceptable only in batch workers and callback firewalls, and they must be explicitly commented as intentional.

### Abstraction Bypass (1 instance)

- **What happened:** A verify-download branch constructed a filesystem Path from a raw database column string, bypassing the storage backend accessor that owns path layout.
- **Rule:** If an abstraction exists for accessing a resource, use it. Constructing paths from DB columns is the same as hardcoding a layout. Use the accessor method.

### Dual Dependencies for Same Concern (1 instance)

- **What happened:** One package used requests for synchronous HTTP and httpx for async HTTP. Two stubbing seams, two error hierarchies, two sets of timeout conventions.
- **Rule:** One HTTP client per package. httpx supports both sync and async. Consolidate to avoid doubled test seams and mental overhead.

---

## Notes

### Unjustified Broad Catches in Batch Context (8 instances)

- **What happened:** Batch workers and progress-hook firewalls used except Exception without a comment explaining why. The catches were correct but looked like oversights.
- **Rule:** When a broad catch is deliberate, comment it: "Batch robustness" or "Callback firewall." The comment distinguishes intent from accident during review.

---

## Low Severity

### Legacy Naming (21 instances across 21 files)

- **What happened:** Old project name persisted in file headers, user-agent strings, function names, environment variables, and docstrings after a rename.
- **Rule:** When renaming a project, grep the entire repo for the old name. Headers, user-agent strings, env vars, logging function names, and docstrings all count. One pass, same commit.

### Dead Code (2 instances)

- **What happened:** A private helper function and an unused import survived a refactor. A module docstring still advertised the deleted function.
- **Rule:** After removing a caller, grep for the callee. After removing a function, update imports and docstrings that reference it. Same commit.

### Inline Magic Numbers / Thresholds (20 instances)

- **What happened:** Scoring penalties, batch timeouts, display limits, and heuristic thresholds were scattered as bare numeric literals across QA, structure, and API modules.
- **Rule:** Every tunable number gets a module-level named constant with a descriptive name. The name documents intent; the value becomes a single place to change.

### Non-Atomic File Writes (1 instance)

- **What happened:** A JSON metrics file was written directly via write_text(). A crash mid-write would leave a corrupt partial file.
- **Rule:** Write to a temp file in the same directory, then os.replace() to the target. This is a one-line pattern; there is no reason to skip it.

### Documentation Drift (2 instances)

- **What happened:** Agent rules and module docstrings named a library ("Instructor") that was replaced months ago by a different one ("Pydantic AI"). The code worked; the docs misled.
- **Rule:** When swapping a dependency, grep docs and docstrings for the old name. CLAUDE.md, README, and module-level docstrings are all code. Update them in the same PR.

### Missing Default Timeout (2 instances)

- **What happened:** HTTP client constructors omitted a default timeout. Per-request timeouts existed but a missed call could hang indefinitely.
- **Rule:** Set a default timeout on the client constructor as a safety net. Per-request timeouts are a refinement, not a substitute.
