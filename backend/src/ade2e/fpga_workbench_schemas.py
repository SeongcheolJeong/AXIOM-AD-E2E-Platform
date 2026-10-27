"""Versioned authoring contracts for bounded serial FPGA dataflow graphs."""
from typing import Annotated, Literal, Union
from pydantic import Field, model_validator
from .ecu_lab_schemas import StrictModel, RevisionRef, RevisionSave
from .rtl_lab_schemas import StallWindow, RtlConfig

ADAPTER = 'verilator-workbench-ab'
FIDELITY = 'rtl_reference_graph'
Identity = Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')]

class Position(StrictModel):
    x: float = Field(ge=-10000, le=10000, allow_inf_nan=False)
    y: float = Field(ge=-10000, le=10000, allow_inf_nan=False)

class SourceParams(StrictModel):
    words: int = Field(default=4096, ge=1, le=16384)
class FifoParams(StrictModel):
    depth: int = Field(default=64, ge=2, le=1024)
class SinkParams(StrictModel):
    pass
class ReadyParams(StrictModel):
    stalls: list[StallWindow] = Field(default_factory=list, max_length=128)

class NodeBase(StrictModel):
    id: Identity
    label: str = Field(default='', max_length=100)
    position: Position
class SourceNode(NodeBase):
    type: Literal['source']
    params: SourceParams = Field(default_factory=SourceParams)
class FifoNode(NodeBase):
    type: Literal['fifo']
    params: FifoParams = Field(default_factory=FifoParams)
class SinkNode(NodeBase):
    type: Literal['sink']
    params: SinkParams = Field(default_factory=SinkParams)
class ReadyNode(NodeBase):
    type: Literal['ready']
    params: ReadyParams = Field(default_factory=ReadyParams)
Node = Annotated[Union[SourceNode, FifoNode, SinkNode, ReadyNode], Field(discriminator='type')]

class Edge(StrictModel):
    id: Identity
    source: Identity
    source_port: Literal['out'] = 'out'
    target: Identity
    target_port: Literal['in', 'ready'] = 'in'

class Graph(StrictModel):
    nodes: list[Node] = Field(default_factory=list, max_length=16)
    edges: list[Edge] = Field(default_factory=list, max_length=32)
    @model_validator(mode='after')
    def identities(self):
        if len({n.id for n in self.nodes}) != len(self.nodes):
            raise ValueError('Graph node IDs must be unique')
        if len({e.id for e in self.edges}) != len(self.edges):
            raise ValueError('Graph edge IDs must be unique')
        return self

class Settings(StrictModel):
    max_cycles: int = Field(default=20000, ge=16, le=100000)
    resets: list[int] = Field(default_factory=lambda: [0, 1], min_length=1, max_length=128)
    @model_validator(mode='after')
    def schedule(self):
        RtlConfig(max_cycles=self.max_cycles, resets=self.resets)
        return self

class Probe(StrictModel):
    id: Identity
    target_type: Literal['node', 'edge']
    target_id: Identity
    signals: list[Annotated[str, Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')]] = Field(min_length=1, max_length=16)
    instrument: Literal['logic', 'data', 'buffer', 'counter']
    @model_validator(mode='after')
    def distinct(self):
        if len(set(self.signals)) != len(self.signals):
            raise ValueError('Probe signals must be unique')
        return self

class WorkbenchSave(RevisionSave):
    graph: Graph
    settings: Settings = Field(default_factory=Settings)
    seed: int = Field(default=42, ge=0, le=2147483647)
    probes: list[Probe] = Field(default_factory=list, max_length=32)
    panel_order: list[Identity] = Field(default_factory=list, max_length=32)
    @model_validator(mode='after')
    def identities(self):
        if len({p.id for p in self.probes}) != len(self.probes):
            raise ValueError('Probe IDs must be unique')
        if len(set(self.panel_order)) != len(self.panel_order):
            raise ValueError('Panel order must not repeat a probe ID')
        return self
