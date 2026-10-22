# Tensor Integration Design for Kosika

## Overview

This document describes how to integrate tensor operations into the Kosika language as a first-class feature, making it competitive with NumPy/PyTorch for AI workloads while maintaining type safety and traceability.

## Goals

1. **Type-safe tensor operations:** Shape checking at compile time where possible
2. **NumPy-compatible:** Broadcasting rules and operations match NumPy
3. **Traceable:** All tensor operations are recorded in traces for replay
4. **Zero-dependency:** Works without NumPy, optionally uses it for performance
5. **AI-native:** Support for common neural network operations

## Language Extensions

### 1. Tensor Type Syntax

Kosika will support a new `tensor` type with optional shape annotation:

```kosika
// Generic tensor (shape unknown at compile time)
tensor T

// Shaped tensor (shape checked at compile time when possible)
tensor T[128, 768]  // 2D tensor: 128x768
tensor T[3, 224, 224]  // 3D tensor for images
tensor T[batch, seq_len, hidden]  // Named dimensions (future)
```

### 2. Tensor Literals

```kosika
// Create from array literal
let weights: tensor Number[2, 3] = [[1, 2, 3], [4, 5, 6]]

// Create with built-in constructors
let zeros: tensor Number[128, 768] = tensor.zeros([128, 768])
let ones: tensor Number[10] = tensor.ones([10])
let full: tensor Number[3, 3] = tensor.full([3, 3], 5.0)
```

### 3. Tensor Operations

#### Element-wise Operations

```kosika
let c = a + b  // element-wise addition with broadcasting
let d = a - b  // element-wise subtraction
let e = a * b  // element-wise multiplication
let f = a / b  // element-wise division
```

#### Matrix Operations

```kosika
let result = a @ b  // matrix multiplication
let transposed = a.transpose()
let reshaped = a.reshape([256, 384])
```

#### Reduction Operations

```kosika
let sum_axis0 = a.sum(axis: 0)
let mean_axis1 = a.mean(axis: 1)
```

#### Activation Functions

```kosika
let activated = a.relu()
let probabilities = a.softmax(axis: -1)
let gated = a.sigmoid()
```

#### Slicing and Concatenation

```kosika
let sliced = a.slice([0:10, :])
let concatenated = tensor.concat([a, b], axis: 0)
let stacked = tensor.stack([a, b], axis: 0)
```

## Implementation Plan

### Phase 1: Parser Extension

**File:** `kosika/parser.py`

Add support for:
- `tensor` type in the type parser
- Tensor literal syntax: `tensor T[shape]`
- Shape expressions: `[d1, d2, d3, ...]`

```python
# New token
TENSOR = "tensor"

# Parse tensor type
def parse_tensor_type(self):
    self.expect(TENSOR)
    self.expect(IDENTIFIER)  # element type
    if self.peek() == LBRACKET:
        self.consume(LBRACKET)
        shape = []
        while self.peek() != RBRACKET:
            shape.append(self.parse_expression())
            if self.peek() == COMMA:
                self.consume(COMMA)
        self.consume(RBRACKET)
        return TensorType(element_type, shape)
    return TensorType(element_type, None)
```

### Phase 2: Type Checker Extension

**File:** `kosika/checker.py`

Add tensor type checking:
- Shape compatibility checking for operations
- Broadcasting validation
- Shape inference where possible

```python
class TensorType(Type):
    def __init__(self, element_type, shape=None):
        self.element_type = element_type
        self.shape = shape  # None or tuple of expressions

    def check_shape_compatible(self, other, operation):
        # Check if shapes are compatible for the given operation
        pass

    def broadcast_shape(self, other):
        # Compute broadcasted shape
        pass
```

### Phase 3: Runtime Integration

**File:** `kosika/runtime.py`

Add tensor operation execution:
- Import the `tensors` module
- Execute tensor operations with shape validation
- Record tensor operations in traces

```python
from . import tensors

def execute_tensor_add(self, a, b):
    result = tensors.add(a, b)
    self.record_tensor_operation("add", a, b, result)
    return result
```

### Phase 4: Builtin Methods Extension

**File:** `kosika/builtin_methods.py`

Add tensor methods to the method table:

```python
TENSOR_METHODS = {
    "shape": ("Shape", [], shape),
    "reshape": ("Tensor", ["shape"], reshape),
    "transpose": ("Tensor", ["axes?"], transpose),
    "sum": ("Tensor", ["axis"], sum_along_axis),
    "mean": ("Tensor", ["axis"], mean_along_axis),
    "softmax": ("Tensor", ["axis?"], softmax),
    "relu": ("Tensor", [], relu),
    "sigmoid": ("Tensor", [], sigmoid),
    "tanh": ("Tensor", [], tanh),
    "gelu": ("Tensor", [], gelu),
    "flatten": ("Tensor", [], flatten),
}
```

### Phase 5: AST Extension

**File:** `kosika/ast.py`

Add new AST nodes:

```python
@dataclass
class TensorType(Type):
    element_type: Type
    shape: Tuple[Expression, ...] | None

@dataclass
class TensorLiteral(Expression):
    value: Any
    tensor_type: TensorType

@dataclass
class TensorMethodCall(Expression):
    receiver: Expression
    method: str
    arguments: List[Expression]
```

## Examples

### Example 1: Simple Neural Network Layer

```kosika
record Input {
  data: tensor Number[batch, 784]
}

record Output {
  data: tensor Number[batch, 10]
}

fn linear_layer(input: tensor Number[batch, in_dim],
                weights: tensor Number[in_dim, out_dim],
                bias: tensor Number[out_dim]) -> tensor Number[batch, out_dim] {
  let result = input @ weights  // matrix multiplication
  let biased = result + bias  // broadcast bias
  return biased.relu()
}

workflow main(input: Input) -> Output {
  step process {
    let weights: tensor Number[784, 10] = tensor.load("weights.kos")
    let bias: tensor Number[10] = tensor.load("bias.kos")
    let output_data = linear_layer(input.data, weights, bias)
    return {data: output_data}
  }
}
```

### Example 2: Attention Mechanism

```kosika
fn attention(query: tensor Number[seq_len, hidden],
             key: tensor Number[seq_len, hidden],
             value: tensor Number[seq_len, hidden]) -> tensor Number[seq_len, hidden] {
  // Compute attention scores
  let scores = query @ key.transpose()  // [seq_len, seq_len]
  let scaled = scores / (hidden.sqrt())  // scaling
  let weights = scaled.softmax(axis: -1)  // [seq_len, seq_len]

  // Apply attention to values
  let attended = weights @ value  // [seq_len, hidden]
  return attended
}
```

### Example 3: Convolution (Future)

```kosika
fn conv2d(input: tensor Number[batch, in_channels, height, width],
          kernel: tensor Number[out_channels, in_channels, kernel_h, kernel_w],
          bias: tensor Number[out_channels]) -> tensor Number[batch, out_channels, out_h, out_w] {
  // Convolution operation
  let convolved = tensor.conv2d(input, kernel, stride: 1, padding: 0)
  let biased = convolved + bias.reshape([1, out_channels, 1, 1])
  return biased.relu()
}
```

## Performance Considerations

1. **NumPy Acceleration:** When NumPy is available, use it for large operations
2. **Shape Caching:** Cache shape computations to avoid repeated checks
3. **Lazy Evaluation:** Consider lazy evaluation for chained operations (future)
4. **Memory Pooling:** Reuse tensor memory where possible (future)

## Testing Strategy

1. **Unit Tests:** Test each tensor operation independently
2. **Shape Tests:** Test shape checking and broadcasting
3. **Compatibility Tests:** Compare results with NumPy
4. **Integration Tests:** Test tensors in workflows and with models
5. **Trace Tests:** Ensure tensor operations are properly traced

## Migration Path

1. **Phase 1:** Implement core tensor operations (add, multiply, reshape, transpose)
2. **Phase 2:** Add activation functions and reductions
3. **Phase 3:** Add advanced operations (softmax, concat, stack)
4. **Phase 4:** Add neural network primitives (convolution, pooling)
5. **Phase 5:** Add gradient computation and autograd (future)

## Success Criteria

- [ ] Can implement a simple neural network without external libraries
- [ ] Performance within 2x of NumPy for common operations
- [ ] Type-safe tensor operations (shape checking at compile time)
- [ ] All tensor operations are traceable and replayable
- [ ] Compatible with existing Kosika type system
- [ ] Works without NumPy, optionally uses it for performance

## Open Questions

1. Should we support named dimensions (e.g., `tensor T[batch, seq_len, hidden]`)?
2. Should we implement automatic differentiation (autograd)?
3. Should we support GPU acceleration directly or through adapters?
4. How should we handle very large tensors that don't fit in memory?

## References

- NumPy documentation: https://numpy.org/doc/
- PyTorch tensor operations: https://pytorch.org/docs/stable/tensors.html
- TensorFlow tensor operations: https://www.tensorflow.org/api_docs/python/tf
