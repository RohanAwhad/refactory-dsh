# Create fidelity and the remote IR boundary

The pinned Create-v2 behavioral contract and the optional remote IR parity
suite test different things. Create conformance checks the source stages,
responsibilities, decisions and artifact flow after the documented native
mapping. IR parity asks whether an unmodified remote schema can represent a
DSH graph. A native Create pass does not imply universal remote IR compatibility.

The source oracle for this investigation is remote-factory
`3ab6f8f7ca792c874f2654574d7f7fd6dd18b896`. Its strict node constructors reject
DSH's `resource_role` and `command_timeout_s` fields. Lowering now omits these
fields when inactive, rather than passing `None`. Its serialized DSH graph is
unchanged. Active resource phases and command deadlines remain in the graph:
discarding them would change execution and is not an acceptable parity fix.

The optional full corpus still exposes these concrete schema boundaries:

| Boundary | Observed result | Treatment |
|---|---|---|
| Inactive DSH resource/deadline fields | Strict remote constructors rejected `None` fields | Omit inactive fields; the common-schema graphs can be compared directly |
| Active resource phases and shell deadlines | The pinned remote schema rejects the extra fields | Keep their native execution semantics and report the remote incompatibility |
| Data-node topology | Remote now derives branches from edges; DSH's existing data IR declares subgraph entry/exit | Separate data/optimization migration, outside full Create; do not silently rewrite it |
| Custom gate labels | The pinned validator rejects `loop_mode/loop` despite the unchanged serialized graph | Record the validator difference; do not alter another mode to make Create acceptance green |

`test_ir_parity.py` continues to expose unresolved corpus failures when the
reference is installed. Its oracle and expectations are not weakened to hide
them. `test_lowering.py`, native resource/deadline tests, and the independent
Create contract tests separately verify the paths they cover. The optional
reference tests require its actual dependencies, including NetworkX; running
without them is unavailable verification, not a schema result.

Public Create adoption must be backed by the full native authoring and delivered
capability evidence. It must not be described as a general IR, data-mode, or
provider compatibility release.
