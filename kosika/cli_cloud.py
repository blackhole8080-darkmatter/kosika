"""
Kosika Cloud CLI Commands

This module provides CLI commands for cloud execution, program management,
and meta-agent selection.
"""

import argparse
import json
import sys
from pathlib import Path

from .cloud import (
    ProgramRegistry,
    create_package,
    register_package,
)
from .meta_agent import select_optimal_configuration


def cmd_register(args):
    """Register a Kosika program package."""
    try:
        package = create_package(Path(args.directory))
        program_id = register_package(package)
        print(f"✓ Registered program: {program_id}")
        print(f"  Version: {package.manifest.version}")
        print(f"  Hash: {package.manifest.hash}")
    except Exception as e:
        print(f"✗ Registration failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_list(args):
    """List available programs."""
    registry = ProgramRegistry()
    programs = registry.list(author=args.author)

    if not programs:
        print("No programs found.")
        return

    print(f"Found {len(programs)} program(s):")
    for program in programs:
        print(f"  • {program['id']}")
        print(f"    Versions: {', '.join(program['versions'])}")
        print(f"    Latest: {program['latest']}")


def cmd_search(args):
    """Search for programs."""
    registry = ProgramRegistry()
    results = registry.search(args.query)

    if not results:
        print(f"No programs found matching '{args.query}'.")
        return

    print(f"Found {len(results)} program(s) matching '{args.query}':")
    for result in results:
        print(f"  • {result['id']} (v{result['version']})")
        print(f"    Capabilities: {', '.join(result['capabilities'])}")


def cmd_select(args):
    """Select optimal agent/model/tools configuration."""
    try:
        context_kwargs = {}
        if args.input_size:
            context_kwargs["input_size"] = args.input_size
        if args.urgency:
            context_kwargs["urgency"] = args.urgency
        if args.budget:
            context_kwargs["budget_limit"] = args.budget
        if args.quality:
            context_kwargs["quality_requirement"] = args.quality
        if args.privacy:
            context_kwargs["privacy_level"] = args.privacy

        result = select_optimal_configuration(args.task, **context_kwargs)

        print("Selection Result:")
        print(f"  Agent: {result.selected_agent}")
        print(f"  Model: {result.selected_model}")
        print(f"  Tools: {', '.join(result.selected_tools)}")
        print(f"  Estimated Cost: ${result.estimated_cost:.4f}")
        print(f"  Estimated Latency: {result.estimated_latency:.1f}s")
        print(f"  Confidence: {result.confidence_score:.2f}")
        print(f"\nReasoning: {result.reasoning}")

        if result.alternatives:
            print("\nAlternatives:")
            for alt in result.alternatives:
                print(f"  • {alt['agent']} with {alt['model']}")
                print(
                    f"    Cost: ${alt['estimated_cost']:.4f}, Latency: {alt['estimated_latency']:.1f}s"
                )
                print(f"    {alt['trade_offs']}")

        if args.json:
            print(
                "\n"
                + json.dumps(
                    {
                        "selected_agent": result.selected_agent,
                        "selected_model": result.selected_model,
                        "selected_tools": result.selected_tools,
                        "configuration": result.configuration,
                        "estimated_cost": result.estimated_cost,
                        "estimated_latency": result.estimated_latency,
                        "confidence_score": result.confidence_score,
                        "alternatives": result.alternatives,
                        "reasoning": result.reasoning,
                    },
                    indent=2,
                )
            )

    except Exception as e:
        print(f"✗ Selection failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_execute(args):
    """Execute a program in the cloud (placeholder)."""
    print("Cloud execution is not yet implemented.")
    print("This would:")
    print("  1. Load the program from registry or URL")
    print("  2. Validate capabilities and permissions")
    print("  3. Execute in isolated environment")
    print("  4. Stream results back to client")
    print("\nFor now, use local execution with: python -m kosika.cli run")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Kosika Cloud CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Register command
    register_parser = subparsers.add_parser(
        "register", help="Register a Kosika program"
    )
    register_parser.add_argument(
        "directory", help="Directory containing Kosika source files"
    )
    register_parser.set_defaults(func=cmd_register)

    # List command
    list_parser = subparsers.add_parser("list", help="List available programs")
    list_parser.add_argument("--author", help="Filter by author")
    list_parser.set_defaults(func=cmd_list)

    # Search command
    search_parser = subparsers.add_parser("search", help="Search for programs")
    search_parser.add_argument("query", help="Search query")
    search_parser.set_defaults(func=cmd_search)

    # Select command
    select_parser = subparsers.add_parser(
        "select", help="Select optimal agent/model/tools"
    )
    select_parser.add_argument("task", help="Task description")
    select_parser.add_argument("--input-size", type=int, help="Input size in bytes")
    select_parser.add_argument(
        "--urgency", choices=["low", "medium", "high"], help="Urgency level"
    )
    select_parser.add_argument("--budget", type=float, help="Budget limit in USD")
    select_parser.add_argument(
        "--quality", choices=["low", "medium", "high"], help="Quality requirement"
    )
    select_parser.add_argument(
        "--privacy", choices=["public", "private", "confidential"], help="Privacy level"
    )
    select_parser.add_argument("--json", action="store_true", help="Output as JSON")
    select_parser.set_defaults(func=cmd_select)

    # Execute command
    execute_parser = subparsers.add_parser("execute", help="Execute program in cloud")
    execute_parser.add_argument("program", help="Program ID or URL")
    execute_parser.add_argument("--input", help="Input data as JSON")
    execute_parser.set_defaults(func=cmd_execute)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
