"""Read the input/output signature of an ONNX file without the onnx package.

An ONNX file is a protobuf ``ModelProto``. Only the handful of fields needed
to answer "what goes in, what comes out" are decoded; tensor data is skipped
without being copied, so even large models are inspected instantly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, List, NamedTuple, Optional, Tuple, Union

Dim = Union[int, str, None]  # fixed size, symbolic name, or unknown


class Tensor(NamedTuple):
    name: str
    dims: Tuple[Dim, ...]

    @property
    def size(self) -> Optional[int]:
        """Number of values per sample: the product of all dimensions after an
        optional leading batch dimension. None if a non-batch dimension is
        not fixed."""
        dims = self.dims[1:] if len(self.dims) > 1 else self.dims
        total = 1
        for d in dims:
            if not isinstance(d, int) or d <= 0:
                return None
            total *= d
        return total

    def __str__(self) -> str:
        return "%s[%s]" % (self.name, ", ".join("?" if d is None else str(d) for d in self.dims))


class ModelInfo(NamedTuple):
    inputs: List[Tensor]
    outputs: List[Tensor]
    opset: Optional[int]
    producer: str


class OnnxError(Exception):
    pass


def _varint(buf: memoryview, i: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if i >= len(buf) or shift > 63:
            raise OnnxError("truncated or corrupt file")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf: memoryview) -> Iterator[Tuple[int, int, object]]:
    """Yield (field number, wire type, value) for one protobuf message."""
    i = 0
    n = len(buf)
    while i < n:
        key, i = _varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 1:
            value, i = buf[i:i + 8], i + 8
        elif wire == 2:
            length, i = _varint(buf, i)
            value, i = buf[i:i + length], i + length
        elif wire == 5:
            value, i = buf[i:i + 4], i + 4
        else:
            raise OnnxError("unsupported protobuf wire type %d" % wire)
        if i > n:
            raise OnnxError("truncated file")
        yield field, wire, value


def _text(value) -> str:
    return bytes(value).decode("utf-8", "replace")


def _shape(buf: memoryview) -> Tuple[Dim, ...]:
    dims: List[Dim] = []
    for field, wire, value in _fields(buf):
        if field == 1 and wire == 2:  # TensorShapeProto.dim
            dim: Dim = None
            for f, w, v in _fields(value):
                if f == 1 and w == 0:  # dim_value (int64)
                    dim = v - (1 << 64) if v >= (1 << 63) else v
                elif f == 2 and w == 2:  # dim_param
                    dim = _text(v)
            dims.append(dim)
    return tuple(dims)


def _value_info(buf: memoryview) -> Tensor:
    name = ""
    dims: Tuple[Dim, ...] = ()
    for field, wire, value in _fields(buf):
        if field == 1 and wire == 2:
            name = _text(value)
        elif field == 2 and wire == 2:  # TypeProto
            for f, w, v in _fields(value):
                if f == 1 and w == 2:  # tensor_type
                    for f2, w2, v2 in _fields(v):
                        if f2 == 2 and w2 == 2:  # shape
                            dims = _shape(v2)
    return Tensor(name, dims)


def _initializer_name(buf: memoryview) -> str:
    for field, wire, value in _fields(buf):
        if field == 8 and wire == 2:  # TensorProto.name
            return _text(value)
    return ""


def describe_bytes(data: bytes) -> ModelInfo:
    buf = memoryview(data)
    graph = None
    opset = None
    producer = ""
    for field, wire, value in _fields(buf):
        if field == 7 and wire == 2:  # ModelProto.graph
            graph = value
        elif field == 2 and wire == 2:
            producer = _text(value)
        elif field == 8 and wire == 2:  # opset_import
            domain, version = "", None
            for f, w, v in _fields(value):
                if f == 1 and w == 2:
                    domain = _text(v)
                elif f == 2 and w == 0:
                    version = v
            if domain in ("", "ai.onnx") and version is not None:
                opset = version
    if graph is None:
        raise OnnxError("not an ONNX model (no graph found)")
    inputs: List[Tensor] = []
    outputs: List[Tensor] = []
    initializers = set()
    for field, wire, value in _fields(graph):
        if wire != 2:
            continue
        if field == 11:
            inputs.append(_value_info(value))
        elif field == 12:
            outputs.append(_value_info(value))
        elif field == 5:
            initializers.add(_initializer_name(value))
    # Old exporters also list the weights as graph inputs; those are not real inputs.
    inputs = [t for t in inputs if t.name not in initializers]
    return ModelInfo(inputs, outputs, opset, producer)


def describe(path: Path) -> ModelInfo:
    try:
        return describe_bytes(Path(path).read_bytes())
    except OnnxError as e:
        raise OnnxError("%s: %s" % (path, e))
    except (IndexError, ValueError) as e:
        raise OnnxError("%s: corrupt file (%s)" % (path, e))
