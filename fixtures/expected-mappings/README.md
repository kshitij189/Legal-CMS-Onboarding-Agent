# Expected mappings (ground truth)

Hand-labelled answers for the fictional firms, used to score mapping proposals (Phase 3) and the
evaluation suite (Phase 6). They are synthetic, author-labelled data — not evidence about real firms.

Each file covers **every** field of the firm's normalized source schema exactly once, against the
default mock CMS target schema (`fixtures/target-schemas/mock-cms-default.json`).

| `decision` | Meaning |
|---|---|
| `map` | Map to `target_field` with `transformation` (and `value_map` for enum targets) |
| `requires_custom_field` | No suitable CMS field exists; a custom field (`proposed_custom_field`) must be created first, after human approval |
| `ambiguous` | Several targets are plausible (`candidates`); a human must decide |
| `no_target` | Nothing in the CMS should receive this field; leave it unmapped and report it |
| `unsupported` | The source type cannot be carried by an intake mapping (e.g. arrays/files) |

`required_target_coverage` lists required CMS fields and which source field satisfies each, so
required-field coverage can be checked.
