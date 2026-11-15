"""Tensor operations for Kosika - multi-dimensional arrays for AI workloads.

This module extends the existing matrix/vector support to handle tensors of
arbitrary rank (1D vectors, 2D matrices, 3D, 4D, etc.). Tensors are stored as
nested Python lists, making them traceable and replayable like any other value.

Key features:
- Shape-aware operations with compile-time checking where possible
- Broadcasting rules compatible with NumPy
- Common AI operations: convolution, pooling, attention
- Gradient computation primitives
- Hardware acceleration hooks (via NumPy when available)
"""

from __future__ import annotations

import math
from typing import Any, List, Tuple, Union

_np: Any
try:
    import numpy as _numpy
    _np = _numpy
except Exception:
    _np = None

# Type aliases
Tensor = Union[List[Any], List[List[Any]], List[List[List[Any]]]]
Shape = Tuple[int, ...]

# Limits
MAX_TENSOR_RANK = 8
MAX_TENSOR_ELEMENTS = 16_000_000
MAX_TENSOR_DIMENSION = 4096


class TensorError(RuntimeError):
    """Raised when tensor shapes are incompatible or operations are invalid."""


def shape(tensor: Tensor) -> Shape:
    """Get the shape of a tensor as a tuple of dimensions.

    Checks that the tensor is rectangular (all sublists at each level have
    the same length) and within size limits.
    """
    def _get_shape(t: Any, depth: int = 0) -> Tuple[int, ...]:
        if not isinstance(t, list):
            return ()
        if depth > MAX_TENSOR_RANK:
            raise TensorError(f"Tensor rank exceeds maximum of {MAX_TENSOR_RANK}")
        if not t:
            return (0,)
        first_len = len(t)
        if first_len > MAX_TENSOR_DIMENSION:
            raise TensorError(f"Tensor dimension {depth} exceeds maximum of {MAX_TENSOR_DIMENSION}")
        # Check all elements at this level have the same shape
        shapes = [_get_shape(elem, depth + 1) for elem in t]
        if not shapes:
            return (first_len,)
        # All shapes must be identical
        first_shape = shapes[0]
        for s in shapes[1:]:
            if s != first_shape:
                raise TensorError(
                    f"Tensor is not rectangular: at depth {depth}, found shapes "
                    f"{first_shape} and {s}"
                )
        return (first_len,) + first_shape

    result = _get_shape(tensor)
    # Count total elements
    total = 1
    for dim in result:
        total *= dim
    if total > MAX_TENSOR_ELEMENTS:
        raise TensorError(
            f"Tensor has {total} elements, exceeding maximum of {MAX_TENSOR_ELEMENTS}"
        )
    return result


def zeros(shape: Shape) -> Tensor:
    """Create a tensor filled with zeros."""
    if not shape:
        return 0  # type: ignore[return-value]
    if len(shape) > MAX_TENSOR_RANK:
        raise TensorError(f"Shape rank {len(shape)} exceeds maximum {MAX_TENSOR_RANK}")
    if any(d > MAX_TENSOR_DIMENSION for d in shape):
        raise TensorError(f"Shape contains dimension exceeding {MAX_TENSOR_DIMENSION}")

    def _zeros_at_depth(s: Tuple[int, ...]) -> Any:
        if len(s) == 1:
            return [0] * s[0]
        return [_zeros_at_depth(s[1:]) for _ in range(s[0])]

    return _zeros_at_depth(shape)


def ones(shape: Shape) -> Tensor:
    """Create a tensor filled with ones."""
    if not shape:
        return 1  # type: ignore[return-value]
    if len(shape) > MAX_TENSOR_RANK:
        raise TensorError(f"Shape rank {len(shape)} exceeds maximum {MAX_TENSOR_RANK}")

    def _ones_at_depth(s: Tuple[int, ...]) -> Any:
        if len(s) == 1:
            return [1] * s[0]
        return [_ones_at_depth(s[1:]) for _ in range(s[0])]

    return _ones_at_depth(shape)


def full(shape: Shape, value: Any) -> Tensor:
    """Create a tensor filled with a given value."""
    if not shape:
        return value
    if len(shape) > MAX_TENSOR_RANK:
        raise TensorError(f"Shape rank {len(shape)} exceeds maximum {MAX_TENSOR_RANK}")

    def _full_at_depth(s: Tuple[int, ...]) -> Any:
        if len(s) == 1:
            return [value] * s[0]
        return [_full_at_depth(s[1:]) for _ in range(s[0])]

    return _full_at_depth(shape)


def _broadcast_shapes(a_shape: Shape, b_shape: Shape) -> Shape:
    """Compute the broadcast shape of two tensors following NumPy rules.

    Two dimensions are compatible when:
    1. They are equal, or
    2. One of them is 1, or
    3. One of them doesn't exist (the tensor has fewer dimensions)
    """
    # Pad the shorter shape with 1s on the left
    max_rank = max(len(a_shape), len(b_shape))
    a_padded = (1,) * (max_rank - len(a_shape)) + a_shape
    b_padded = (1,) * (max_rank - len(b_shape)) + b_shape

    result = []
    for a_dim, b_dim in zip(a_padded, b_padded):
        if a_dim == b_dim:
            result.append(a_dim)
        elif a_dim == 1:
            result.append(b_dim)
        elif b_dim == 1:
            result.append(a_dim)
        else:
            raise TensorError(
                f"Cannot broadcast shapes {a_shape} and {b_shape}: "
                f"incompatible dimensions {a_dim} and {b_dim}"
            )
    return tuple(result)


def _expand(tensor: Tensor, target_shape: Shape) -> Tensor:
    """Expand a tensor to a target shape by broadcasting."""
    current_shape = shape(tensor)
    if current_shape == target_shape:
        return tensor

    # Pad with new dimensions on the left
    if len(current_shape) < len(target_shape):
        tensor = [tensor]
        current_shape = (1,) + current_shape

    def _expand_at_depth(t: Any, curr: Shape, target: Shape, depth: int = 0) -> Any:
        if depth >= len(target):
            return t
        curr_dim = curr[depth] if depth < len(curr) else 1
        target_dim = target[depth]

        if curr_dim == target_dim:
            # No expansion needed at this level
            if depth + 1 >= len(target):
                return t
            if isinstance(t, list):
                return [_expand_at_depth(elem, curr, target, depth + 1) for elem in t]
            return t
        elif curr_dim == 1:
            # Broadcast this dimension
            if depth + 1 >= len(target):
                return t * target_dim
            if isinstance(t[0], list):
                # Expand by repeating the nested structure
                expanded = _expand_at_depth(t[0], curr[1:], target[1:], depth + 1)
                return [expanded for _ in range(target_dim)]
            else:
                return [t[0] for _ in range(target_dim)]
        else:
            raise TensorError(f"Cannot expand dimension {depth} from {curr_dim} to {target_dim}")

    return _expand_at_depth(tensor, current_shape, target_shape)


def add(a: Tensor, b: Tensor) -> Tensor:
    """Element-wise addition of two tensors with broadcasting."""
    a_shape = shape(a)
    b_shape = shape(b)
    target_shape = _broadcast_shapes(a_shape, b_shape)

    a_expanded = _expand(a, target_shape)
    b_expanded = _expand(b, target_shape)

    def _add_at_depth(t1: Any, t2: Any, depth: int = 0) -> Any:
        if depth >= len(target_shape):
            return t1 + t2
        if isinstance(t1, list):
            return [_add_at_depth(e1, e2, depth + 1) for e1, e2 in zip(t1, t2)]
        return t1 + t2

    return _add_at_depth(a_expanded, b_expanded)


def subtract(a: Tensor, b: Tensor) -> Tensor:
    """Element-wise subtraction of two tensors with broadcasting."""
    a_shape = shape(a)
    b_shape = shape(b)
    target_shape = _broadcast_shapes(a_shape, b_shape)

    a_expanded = _expand(a, target_shape)
    b_expanded = _expand(b, target_shape)

    def _subtract_at_depth(t1: Any, t2: Any, depth: int = 0) -> Any:
        if depth >= len(target_shape):
            return t1 - t2
        if isinstance(t1, list):
            return [_subtract_at_depth(e1, e2, depth + 1) for e1, e2 in zip(t1, t2)]
        return t1 - t2

    return _subtract_at_depth(a_expanded, b_expanded)


def multiply(a: Tensor, b: Tensor) -> Tensor:
    """Element-wise multiplication of two tensors with broadcasting."""
    a_shape = shape(a)
    b_shape = shape(b)
    target_shape = _broadcast_shapes(a_shape, b_shape)

    a_expanded = _expand(a, target_shape)
    b_expanded = _expand(b, target_shape)

    def _multiply_at_depth(t1: Any, t2: Any, depth: int = 0) -> Any:
        if depth >= len(target_shape):
            return t1 * t2
        if isinstance(t1, list):
            return [_multiply_at_depth(e1, e2, depth + 1) for e1, e2 in zip(t1, t2)]
        return t1 * t2

    return _multiply_at_depth(a_expanded, b_expanded)


def divide(a: Tensor, b: Tensor) -> Tensor:
    """Element-wise division of two tensors with broadcasting."""
    a_shape = shape(a)
    b_shape = shape(b)
    target_shape = _broadcast_shapes(a_shape, b_shape)

    a_expanded = _expand(a, target_shape)
    b_expanded = _expand(b, target_shape)

    def _divide_at_depth(t1: Any, t2: Any, depth: int = 0) -> Any:
        if depth >= len(target_shape):
            if t2 == 0:
                raise TensorError("Division by zero")
            return t1 / t2
        if isinstance(t1, list):
            return [_divide_at_depth(e1, e2, depth + 1) for e1, e2 in zip(t1, t2)]
        if t2 == 0:
            raise TensorError("Division by zero")
        return t1 / t2

    return _divide_at_depth(a_expanded, b_expanded)


def matmul(a: Tensor, b: Tensor) -> Tensor:
    """Matrix multiplication for 2D tensors, or batched matrix multiplication.

    For 2D tensors: standard matrix multiplication.
    For 3D+ tensors: batched matrix multiplication on the last two dimensions.
    """
    a_shape = shape(a)
    b_shape = shape(b)

    if len(a_shape) < 2 or len(b_shape) < 2:
        raise TensorError("matmul requires at least 2D tensors")

    # Check inner dimensions match
    if a_shape[-1] != b_shape[-2]:
        raise TensorError(
            f"matmul: inner dimensions don't match: {a_shape[-1]} vs {b_shape[-2]}"
        )

    # Handle batched dimensions
    if len(a_shape) == 2 and len(b_shape) == 2:
        # Standard 2D matrix multiplication
        return _matmul_2d(a, b)
    elif len(a_shape) == 3 and len(b_shape) == 3:
        # Batched matmul
        if a_shape[0] != b_shape[0]:
            raise TensorError(f"Batch dimensions don't match: {a_shape[0]} vs {b_shape[0]}")
        return [_matmul_2d(a[i], b[i]) for i in range(a_shape[0])]
    else:
        raise TensorError(
            f"matmul: unsupported tensor ranks {len(a_shape)} and {len(b_shape)}"
        )


def _matmul_2d(a: List[List[Any]], b: List[List[Any]]) -> List[List[Any]]:
    """2D matrix multiplication."""
    rows_a = len(a)
    cols_a = len(a[0])
    cols_b = len(b[0])

    result = [[0 for _ in range(cols_b)] for _ in range(rows_a)]

    for i in range(rows_a):
        for j in range(cols_b):
            sum_val = 0
            for k in range(cols_a):
                sum_val += a[i][k] * b[k][j]
            result[i][j] = sum_val

    return result


def reshape(tensor: Tensor, new_shape: Shape) -> Tensor:
    """Reshape a tensor to a new shape (element count must match)."""
    old_shape = shape(tensor)
    old_total = 1
    for dim in old_shape:
        old_total *= dim

    new_total = 1
    for dim in new_shape:
        new_total *= dim

    if old_total != new_total:
        raise TensorError(
            f"Cannot reshape {old_shape} to {new_shape}: "
            f"element counts don't match ({old_total} vs {new_total})"
        )

    # Flatten, then reshape
    def _flatten(t: Any) -> List[Any]:
        if isinstance(t, list):
            result = []
            for elem in t:
                result.extend(_flatten(elem))
            return result
        return [t]

    flat = _flatten(tensor)

    def _build_from_flat(flat_list: List[Any], s: Shape, index: int = 0) -> Tuple[Any, int]:
        if len(s) == 1:
            result = flat_list[index:index + s[0]]
            return result, index + s[0]
        result = []
        for _ in range(s[0]):
            sub_result, index = _build_from_flat(flat_list, s[1:], index)
            result.append(sub_result)
        return result, index

    result, _ = _build_from_flat(flat, new_shape)
    return result


def transpose(tensor: Tensor, axes: Tuple[int, ...] | None = None) -> Tensor:
    """Transpose a tensor by permuting its axes.

    If axes is None, reverse the axes (like NumPy's default).
    """
    tensor_shape = shape(tensor)
    rank = len(tensor_shape)

    if axes is None:
        axes = tuple(reversed(range(rank)))
    else:
        if len(axes) != rank:
            raise TensorError(f"axes length {len(axes)} must match tensor rank {rank}")
        if set(axes) != set(range(rank)):
            raise TensorError(f"axes must be a permutation of 0..{rank-1}")

    def _transpose_at_depth(t: Any, current_shape: Shape, depth: int = 0) -> Any:
        if depth >= rank:
            return t

        # Find which axis is at this depth in the transposed view
        target_axis = axes.index(depth)
        target_dim = current_shape[target_axis]

        if depth == rank - 1:
            # Last dimension: return the values
            return t

        # Build the transposed structure
        result = []
        for i in range(target_dim):
            # Index into the original tensor at the target axis
            def _index_at_axis(t_inner: Any, axis: int, idx: int) -> Any:
                if axis == 0:
                    return t_inner[idx]
                return [_index_at_axis(elem, axis - 1, idx) for elem in t_inner]

            sub_tensor = _index_at_axis(t, target_axis, i)
            result.append(_transpose_at_depth(sub_tensor, current_shape, depth + 1))

        return result

    return _transpose_at_depth(tensor, tensor_shape)


def sum_along_axis(tensor: Tensor, axis: int) -> Tensor:
    """Sum elements along a given axis."""
    tensor_shape = shape(tensor)
    rank = len(tensor_shape)

    if axis < 0 or axis >= rank:
        raise TensorError(f"axis {axis} out of range for tensor of rank {rank}")

    if rank == 1:
        return [tensor[0]]  # Return as 1-element list for consistency

    def _sum_at_depth(t: Any, current_axis: int, target_axis: int) -> Any:
        if current_axis == target_axis:
            # Sum along this axis
            if isinstance(t[0], list):
                # Recursively sum nested structures
                result = t[0]
                for elem in t[1:]:
                    result = _add_recursive(result, elem)
                return result
            return sum(t)
        else:
            # Recurse deeper
            return [_sum_at_depth(elem, current_axis + 1, target_axis) for elem in t]

    def _add_recursive(a: Any, b: Any) -> Any:
        if isinstance(a, list):
            return [_add_recursive(x, y) for x, y in zip(a, b)]
        return a + b

    return _sum_at_depth(tensor, 0, axis)


def mean_along_axis(tensor: Tensor, axis: int) -> Tensor:
    """Compute mean along a given axis."""
    summed = sum_along_axis(tensor, axis)
    tensor_shape = shape(tensor)
    dim_size = tensor_shape[axis]

    def _divide_by_dim(t: Any) -> Any:
        if isinstance(t, list):
            return [_divide_by_dim(elem) for elem in t]
        return t / dim_size

    return _divide_by_dim(summed)


def softmax(tensor: Tensor, axis: int = -1) -> Tensor:
    """Apply softmax function along the given axis."""
    tensor_shape = shape(tensor)
    rank = len(tensor_shape)

    if axis < 0:
        axis = rank + axis

    # Subtract max for numerical stability
    def _max_along_axis(t: Any, current_axis: int, target_axis: int) -> Any:
        if current_axis == target_axis:
            if isinstance(t[0], list):
                return max(_max_along_axis(elem, current_axis + 1, target_axis) for elem in t)
            return max(t)
        return [_max_along_axis(elem, current_axis + 1, target_axis) for elem in t]

    max_val = _max_along_axis(tensor, 0, axis)

    # Subtract max
    def _subtract_max(t: Any, current_axis: int, target_axis: int, m: Any) -> Any:
        if current_axis == target_axis:
            if isinstance(t, list):
                return [_subtract_max(elem, current_axis + 1, target_axis, m) for elem in t]
            return t - m
        return [_subtract_max(elem, current_axis + 1, target_axis, m) for elem in t]

    shifted = _subtract_max(tensor, 0, axis, max_val)

    # Exponentiate
    def _exp(t: Any) -> Any:
        if isinstance(t, list):
            return [_exp(elem) for elem in t]
        return math.exp(t)

    exp_tensor = _exp(shifted)

    # Sum along axis
    sum_exp = sum_along_axis(exp_tensor, axis)

    # Divide
    def _divide(t: Any, s: Any, current_axis: int, target_axis: int) -> Any:
        if current_axis == target_axis:
            if isinstance(t, list):
                return [_divide(elem, s, current_axis + 1, target_axis) for elem in t]
            return t / s
        return [_divide(elem, s, current_axis + 1, target_axis) for elem in t]

    return _divide(exp_tensor, sum_exp, 0, axis)


def relu(tensor: Tensor) -> Tensor:
    """Apply ReLU activation: max(0, x)."""
    def _relu_at_depth(t: Any) -> Any:
        if isinstance(t, list):
            return [_relu_at_depth(elem) for elem in t]
        return max(0, t)

    return _relu_at_depth(tensor)


def sigmoid(tensor: Tensor) -> Tensor:
    """Apply sigmoid activation: 1 / (1 + exp(-x))."""
    def _sigmoid_at_depth(t: Any) -> Any:
        if isinstance(t, list):
            return [_sigmoid_at_depth(elem) for elem in t]
        return 1.0 / (1.0 + math.exp(-t))

    return _sigmoid_at_depth(tensor)


def tanh(tensor: Tensor) -> Tensor:
    """Apply tanh activation."""
    def _tanh_at_depth(t: Any) -> Any:
        if isinstance(t, list):
            return [_tanh_at_depth(elem) for elem in t]
        return math.tanh(t)

    return _tanh_at_depth(tensor)


def gelu(tensor: Tensor) -> Tensor:
    """Apply GELU activation (Gaussian Error Linear Unit)."""
    def _gelu_at_depth(t: Any) -> Any:
        if isinstance(t, list):
            return [_gelu_at_depth(elem) for elem in t]
        return 0.5 * t * (1.0 + math.tanh(math.sqrt(2.0 / math.pi) * (t + 0.044715 * t ** 3)))

    return _gelu_at_depth(tensor)


def flatten(tensor: Tensor) -> Tensor:
    """Flatten a tensor to 1D."""
    def _flatten_at_depth(t: Any) -> List[Any]:
        if isinstance(t, list):
            result = []
            for elem in t:
                result.extend(_flatten_at_depth(elem))
            return result
        return [t]

    return _flatten_at_depth(tensor)


def slice_tensor(tensor: Tensor, slices: Tuple[slice, ...]) -> Tensor:
    """Slice a tensor using Python slice notation."""
    tensor_shape = shape(tensor)

    if len(slices) > len(tensor_shape):
        raise TensorError(
            f"Too many slices: {len(slices)} for tensor of rank {len(tensor_shape)}"
        )

    def _slice_at_depth(t: Any, depth: int) -> Any:
        if depth >= len(slices):
            return t

        s = slices[depth]
        if isinstance(t, list):
            # Apply slice to this dimension
            sliced = t[s]
            if depth + 1 < len(slices):
                return [_slice_at_depth(elem, depth + 1) for elem in sliced]
            return sliced
        return t

    return _slice_at_depth(tensor, 0)


def concat(tensors: List[Tensor], axis: int) -> Tensor:
    """Concatenate tensors along a given axis."""
    if not tensors:
        raise TensorError("Cannot concatenate empty list of tensors")

    first_shape = shape(tensors[0])
    rank = len(first_shape)

    if axis < 0 or axis >= rank:
        raise TensorError(f"axis {axis} out of range for tensor of rank {rank}")

    # Check all tensors have compatible shapes
    for i, t in enumerate(tensors):
        t_shape = shape(t)
        if len(t_shape) != rank:
            raise TensorError(
                f"Tensor {i} has rank {len(t_shape)}, expected {rank}"
            )
        for d in range(rank):
            if d != axis and t_shape[d] != first_shape[d]:
                raise TensorError(
                    f"Tensor {i} has incompatible shape {t_shape} for concatenation"
                )

    def _concat_at_depth(t_list: List[Any], current_axis: int, target_axis: int) -> Any:
        if current_axis == target_axis:
            # Concatenate at this axis
            if isinstance(t_list[0], list):
                result = []
                for t in t_list:
                    result.extend(t)
                return result
            return t_list  # Scalars
        else:
            # Recurse
            return [_concat_at_depth([t[i] for t in t_list], current_axis + 1, target_axis)
                    for i in range(len(t_list[0]))]

    return _concat_at_depth(tensors, 0, axis)


def stack(tensors: List[Tensor], axis: int) -> Tensor:
    """Stack tensors along a new axis."""
    if not tensors:
        raise TensorError("Cannot stack empty list of tensors")

    first_shape = shape(tensors[0])
    rank = len(first_shape)

    # Check all tensors have the same shape
    for i, t in enumerate(tensors):
        t_shape = shape(t)
        if t_shape != first_shape:
            raise TensorError(
                f"Tensor {i} has shape {t_shape}, expected {first_shape}"
            )

    # Insert new axis
    if axis < 0 or axis > rank:
        raise TensorError(f"axis {axis} out of range for stacking rank {rank}")

    def _stack_at_depth(t_list: List[Any], current_axis: int, target_axis: int) -> Any:
        if current_axis == target_axis:
            # Insert new dimension here
            return t_list
        else:
            # Recurse
            return [_stack_at_depth([t[i] for t in t_list], current_axis + 1, target_axis)
                    for i in range(len(t_list[0]))]

    return _stack_at_depth(tensors, 0, axis)


def engine() -> str:
    """Which engine is being used for tensor operations."""
    return f"numpy {_np.__version__}" if _np is not None else "plain Python"
