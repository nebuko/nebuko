| ID | Kind | Statement | Source | Origin |
|---|---|---|---|---|
| R1 | requirement | The analyze step emits a requirements ledger | "the analyze step emits a `requirements` ledger" | explicit |
| R2 | requirement | The plan step emits a coverage table | "The plan gets a `coverage` output field" | explicit |
| R3 | requirement | The engine refuses a plan finalize with missing IDs | "refuse `finalize` in the same turn" | explicit |
| K1 | constraint | No schema migration and no new dependencies | "no schema migration, no new structured store" | explicit |
| K2 | constraint | Fits the existing output.fields mechanism | "works with `output.fields` in `spec-tdd.yaml`" | explicit |
| NG1 | non-goal | No typed ledger persisted in WorkflowState | "Approaches considered: verdict ❌" | explicit |
| Q1 | quality goal | Cost over latency over simplicity | issue thread ranking | inferred |
