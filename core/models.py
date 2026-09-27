"""Typed acquisition boundary; preserve legacy dictionary and HDF5 formats."""
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, TypedDict


class ChannelSpec(TypedDict, total=False):
    label: str
    device: str
    attribute: str
    unit: str
    axis: str
    enabled: bool
    trigger_cmd: str


@dataclass(frozen=True)
class ScanRequest:
    config: dict[str, Any]
    setup: dict[str, Any]

    @classmethod
    def snapshot(cls, config: Mapping[str, Any], setup: Mapping[str, Any]):
        """Workers own a snapshot; later UI edits cannot change an active run."""
        return cls(deepcopy(dict(config)), deepcopy(dict(setup)))
