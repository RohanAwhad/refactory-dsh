from refactory_dsh import nn


class Offset(nn.Mode):
    name = "run"
    description = "Original integer +1 transform"
    unscored_reason = "Produces an artifact"

    @classmethod
    def declare(cls):
        return {"values_path": nn.Knob("", "str", description="Runtime JSON array of integers")}

    def build(self):
        graph = nn.Graph()
        graph.add(nn.Node("adjust", "op", call="refactory_packages.offset_probe.ops:adjust", knobs=("values_path",), writes=("adjusted.json",)))
        return graph

    def invariants(self):
        return (nn.artifact_produced("adjusted.json", by="adjust"),)


class Identity(nn.Mode):
    name = "identity"
    description = "Return the original integer array unchanged"
    unscored_reason = "Produces an artifact"

    @classmethod
    def declare(cls):
        return {"values_path": nn.Knob("", "str", description="Runtime JSON array of integers")}

    def build(self):
        graph = nn.Graph()
        graph.add(nn.Node("identity", "op", call="refactory_packages.offset_probe.ops:identity", knobs=("values_path",), writes=("identity.json",)))
        return graph

    def invariants(self):
        return (nn.artifact_produced("identity.json", by="identity"),)
