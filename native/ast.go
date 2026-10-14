package main

// What a program is once it has been read: its records, functions, tools,
// agents, workflows and tests. Models, swarms, memories, boards and imports
// are not read yet, and a file that has one is refused by name.

type Expr interface{}

// A Number is whole (Int, of any size) or a decimal (Float).
type Literal struct{ Value Value }
type VarExpr struct{ Name string }
type FieldExpr struct {
	Target Expr
	Name   string
}
type IndexExpr struct{ Target, Index Expr }

// An Arg's Name is "" when it was written without one.
type Arg struct {
	Name  string
	Value Expr
}
type CallExpr struct {
	Name string
	Args []Arg
	Line int
}
type MethodExpr struct {
	Target Expr
	Name   string
	Args   []Arg
	Line   int
}
type BinaryExpr struct {
	Left  Expr
	Op    string
	Right Expr
}
type UnaryExpr struct {
	Op      string
	Operand Expr
}
type CondExpr struct{ Cond, Then, Else Expr }
type MatchArm struct {
	Any     bool
	Pattern Value
	Value   Expr
}
type MatchExpr struct {
	Subject Expr
	Arms    []MatchArm
}
type ForExpr struct {
	Item   string
	Seq    Expr
	Body   Expr
	Limit  int
	Where  Expr
	Capped bool
}
type RepeatExpr struct{ Count, Body Expr }
type BlockExpr struct {
	Stmts  []Expr // Binding, Assign or an expression
	Result Expr   // nil when the block ends in a statement
}
type BreakExpr struct{}
type ContinueExpr struct{}
type ReturnExpr struct{ Value Expr }
type FailExpr struct{ Reason Expr }
type TryExpr struct {
	Body      Expr
	Retry     int
	Otherwise Expr
	ErrorName string
}
type ArrayExpr struct{ Items []Expr }
type RecordField struct {
	Name  string
	Value Expr
}
type RecordExpr struct {
	Fields []RecordField
	Base   Expr
}
type FuncRefExpr struct{ Name string }
type FuncExpr struct {
	Params []Param
	Out    string
	Body   Expr
}

type Binding struct {
	Name    string
	Type    string
	Value   Expr
	Mutable bool
}
type Assign struct {
	Target string
	Value  Expr
}

type Param struct{ Name, Type string }

type FuncDecl struct {
	Name    string
	Params  []Param
	Out     string
	Body    []Expr // in the order written
	Nested  []*FuncDecl
	Return  Expr
	Recurse int // 0 when no depth was stated
	Line    int
}

type RecordDecl struct {
	Name    string
	Fields  []Param
	Methods []string
}

type TestStep struct {
	Expect bool
	Value  Expr
	Target string
	Type   string
	Line   int
	Text   string
}

// An Answer is a test's `answers TOOL: [...]`: what a tool says, in order.
type Answer struct {
	Name  string
	Value Expr
}

type TestDecl struct {
	Name    string
	Answers []Answer
	Steps   []TestStep
	Line    int
	Live    bool
}

// A ToolDecl's Source is the MCP server's name, or "system:KIND".
type ToolDecl struct {
	Name, Source, Operation, Access, Output, Input string // Input "" when none
}

type AgentDecl struct {
	Name          string
	Can, Cannot   []string
	Model         string // "" when none
	MaxSteps      int
	MaxToolCalls  int
	MaxModelCalls int
	Instructions  *string
	Deadline      *float64
	Plan, Check   string
}

// valueCall is the name a value written where a call belongs is given, as
// the reference names it.
const valueCall = "$value"

// A StepCall is what a step does: call a function (Kind "function"), run
// a workflow ("workflow") or use a tool through an agent ("tool").
type StepCall struct {
	Kind  string
	Name  string // the function, workflow or tool
	Agent string
	Args  []Arg
	Line  int
}

type Step struct {
	Name        string
	Call        *StepCall
	Binding     string
	BindingType string
	Return      string
}

// A Budget is one ceiling a workflow puts on its own run.
type Budget struct {
	Kind   string // model_calls, tool_calls or seconds
	Amount Value  // a whole number or a decimal
}

type WorkflowDecl struct {
	Name     string
	Params   []Param
	Out      string
	Steps    []*Step
	Approval bool
	Budget   []Budget
}

type Program struct {
	Records   []*RecordDecl
	Tools     []*ToolDecl
	Agents    []*AgentDecl
	Workflows []*WorkflowDecl
	Functions []*FuncDecl
	Tests     []*TestDecl
}
