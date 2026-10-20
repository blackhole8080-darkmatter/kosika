package main

// What a program is once it has been read: its models, records, functions,
// tools, agents, workflows, memories, boards, imports and tests. Swarms
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
	// Defaults are the parameters' values when left out: see defaults.go.
	Defaults map[string]Expr
}

type RecordDecl struct {
	Name    string
	Fields  []Param
	Methods []string
	// Defaults are the fields' values when left out: see defaults.go.
	Defaults map[string]Expr
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
// a workflow ("workflow"), use a tool through an agent ("tool"), ask a
// model ("model") or ask an agent ("agent").
type StepCall struct {
	Kind  string
	Name  string // the function, workflow, tool or model
	Agent string
	Args  []Arg
	Line  int
	// A thinking step (Kind "think") before it is made a model call: its
	// verb, the record `extract` makes, and the models named with `using`.
	Verb   string
	Record string
	Model  string
	More   []string
}

// A Branch is one case of a match step: the call made when the subject
// equals Pattern, or always when Any.
type Branch struct {
	Any     bool
	Pattern Value
	Call    *StepCall
}

type Step struct {
	Name        string
	Call        *StepCall
	Binding     string
	BindingType string
	Return      string
	// Retries is how many more times a model is asked when its answer is
	// refused: `retry N`.
	Retries int
	// Verify is the program's own check of a model's answer: `verify
	// CHECK(...)`, a function returning the reason it refuses the answer,
	// or "" when the answer is fine. Nil when there is none.
	Verify *StepCall
	// Votes is how many times a model is asked, the answer more than half
	// agree on being kept: `vote N`, 0 when it is asked once. Voters are
	// the models that take turns, from a thinking step's `using a, b`.
	Votes  int
	Voters []string
	// Escalate is the model asked when every answer of the step's own was
	// refused: `escalate NAME`, "" when there is none.
	Escalate string
	// Fallback is the call made instead when Call fails: `otherwise`.
	Fallback *StepCall
	// A match step makes one of its Branches' calls, chosen by Subject.
	// Call is then the first branch's.
	Subject  Expr
	Branches []Branch
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

// A ModelDecl is a model a step may ask, with the records it takes and
// gives. A parameter left out is nil.
type ModelDecl struct {
	Name, Input, Output string
	Deployment          *string
	Temperature         *float64
	MaxOutputTokens     *int
	TopP                *float64
	FrequencyPenalty    *float64
	PresencePenalty     *float64
	Stop                []string
	// Default is `default model NAME`: the one a thinking step asks when
	// it names none and several are declared.
	Default bool
	// A model made for a thinking step names, as Provider, the declared
	// model whose answers it gives, and the Verb it was made for.
	Provider, Verb string
}

type Program struct {
	Models    []*ModelDecl
	Records   []*RecordDecl
	Tools     []*ToolDecl
	Agents    []*AgentDecl
	Workflows []*WorkflowDecl
	Functions []*FuncDecl
	Tests     []*TestDecl
	// `memory NAME of Record` and `board NAME of Record`, before
	// LowerStores makes them tools.
	Memories, Boards []*StoreDecl
	// `import "path.kos"` and `export [names]`: see modules.go.
	Imports, Exports []string
	// `enum Size { small, medium, large }`: see enums.go.
	Enums []*EnumDecl
	// `shape Priced { fn price(self) -> Number }`: see shapes.go.
	Shapes []*ShapeDecl
}
