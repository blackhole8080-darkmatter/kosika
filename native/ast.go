package main

// What a program is once it has been read: its records, its functions and
// its tests, each a tree of expressions. This is the core of Kosika: what
// can be worked out with no model and no tool. Workflows, agents, tools and
// models are not read yet, and a file that has them is refused by name.

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

type TestDecl struct {
	Name  string
	Steps []TestStep
	Line  int
	Live  bool
}

type Program struct {
	Records   []*RecordDecl
	Functions []*FuncDecl
	Tests     []*TestDecl
}
