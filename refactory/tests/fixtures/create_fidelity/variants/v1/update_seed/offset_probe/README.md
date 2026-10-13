# Original offset probe

This supplied original package is the intended input for an existing-mode revision.
`offset_probe/run` reads runtime `values_path` JSON containing exact integers,
adds **1** and writes `adjusted.json` into the invocation directory.
`offset_probe/identity` writes the input unchanged to `identity.json`.
Native discovery and invocation use `refactory_dsh.registry` and
`refactory_dsh.create_fidelity_ops`; neither mode requires a model.
This baseline is not the requested revised +7 deliverable.
