# TOMTIT-Code — Product Naming V1

## 0. Document governance

```yaml
document_name: TOMTIT-Code — Product Naming
document_version: V1
document_status: ACTIVE
supersedes_naming_in: none (records a rename, rewrites no frozen artifact)
```

---

## 1. Current names

Current product / repository name:

```text
TOMTIT-Code
```

Current distribution name (`pyproject.toml`):

```text
tomtit-code
```

The Python package directory and import namespace are **unchanged**:

```text
src/tomtit/
import tomtit
```

The distribution rename does not rename the import namespace.

---

## 2. Historical names

The following names are the former product identity. They remain valid inside
historical artifacts and must not be rewritten solely to replace the old name:

```text
TOMTIT-Coordinator
TOMTIT Coordinator
tomtit-coordinator
```

Artifacts that legitimately retain the historical name:

- Frozen, hash-governed V1 documents whose approved SHA-256 values are recorded
  as external gate evidence:
  - `docs/OFFICIAL_BUILD_AND_AI_CODING_PROCESS_V1.md`
  - `docs/EVALUATION_CONTRACT_V1.md`
  - `docs/VERIFIER_POLICY_V1.md`
- Gate and review evidence under `evidence/`.
- Historical report and review documents at the repository root.

Rewriting any of the above would invalidate recorded approval hashes.

---

## 3. Internal architectural terminology

The standalone terms

```text
Coordinator
coordinator
```

name the **Coordinator role** defined in `SYSTEM.md` section 4. They are internal
architectural terminology, not product identity, and are deliberately preserved.

This includes, among others:

- the machine-readable preflight failure code `WORKSPACE_INSIDE_COORDINATOR`

A global replacement of `Coordinator` / `coordinator` is **not** authorized.

---

## 4. Rename rule

Replace the historical product name only where it denotes the product or the
distribution. Leave it in place where it denotes a historical record, a frozen
document, or the internal Coordinator role.
