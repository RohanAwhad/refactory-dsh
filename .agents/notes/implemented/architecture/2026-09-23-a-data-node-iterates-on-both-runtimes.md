# Agent Note: A data node iterates on both runtimes

Status: implemented

## Problem

A mode could declare a `data` node and the walker executed it, but DSH did not: the spine handed the node's view to the driving agent and the cursor moved on to the data node's successor, so the subgraph was never entered. A dataset mode on the shipping runtime therefore selected nothing, iterated nothing, and still reported that it had finished. remote-factory's `WorkflowExecutor._execute_data` iterates the subgraph once per item, and that difference between the runtimes is what the port exists to remove.

## Decision

**The spine iterates a data node, and the item loop is three commands.** The cursor cannot enter a subgraph on its own, so the plugin drives the loop: a `DataNode` in the cursor is asked for its count (`<dataCommand> list --node <id>`), `startPass` puts the cursor on `subgraph_entry`, `begin --index <n>` writes the item to `current_item.json` and runs the Task's `setup`, the ordinary cursor walks the subgraph, completing `subgraph_exit` ends one pass, and `end --index <n>` runs the Task's `verify` and folds `item_scores` into the run's facts. `endPass` then starts the next item, or completes the data node and reaches its successor. An empty selection completes the node and continues: zero items is a fact about the data, not a crash. The item travels in the `wf_next` payload, so a subagent is handed the item it is working on rather than being sent to find it.

**The loader is one implementation, and it is a command rather than a service.** `refactory_dsh.items` calls `refactory_dsh.nn.executor.load_items`, the same function the walker calls, so `inline_items`, a `source_path` read as `directory`/`jsonl`/`csv`, and a Task's `instances()` select identically on either runtime. It is a command because the composition already supplies commands for deterministic nodes and this is the same kind of thing: the graph says which items a node selected, and the command that reads them is deployment state. `DataNode` gained no field in the IR — the node's source travels in the run directory's declarations, where the facts the IR cannot carry already travel.

**A graph with a data node and no loader is refused at load.** The alternative — handing the node to the model as before — is a run that looks like it worked. Both facts it depends on, `executeCommands` and `dataCommand`, are configuration, so the refusal belongs at load.

**The pass machine's rules are checked, not assumed.** `validateGraph` refuses a `subgraph_exit` that is a gate (a pass ends by completing its exit, and a gate is completed with a verdict), an entry that cannot reach its exit, a subgraph node that does not exist, and an explicit edge from the data node into its own subgraph, which would run the first node twice per item. It also follows `subgraph_entry` when deciding reachability, without which a whole subgraph reads as unreachable.

## Consequences

Both runtimes now produce the same facts for a data-driven mode, and `refactory/tests/test_equivalence.py` runs one on each and compares them. Two differences remain, both stated where a reader meets them: the walker runs every pass in one process while DSH starts a process per verb, and neither honours the node's `parallelism`, so passes run one after another. Fixing the loader exposed a live defect: `load_items` called `json.loads` for a JSONL source without importing `json`, so every JSONL selection raised `NameError`. No mode had used one, which is why it survived.

## Alternatives considered

Giving `DataNode` a `command` in the IR was rejected: it would change the language both repositories share in order to carry something the declarations already carry, and the loop needs three verbs rather than one command. Making the loader a capability seam was rejected as a service with one implementation and one consumer. Leaving the node with the driving agent was rejected because the cursor never enters the subgraph, so the instruction cannot be followed at all.
