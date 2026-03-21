#!/usr/bin/env python3
"""
docksmith - A simplified Docker-like build and runtime system.
Usage:
  docksmith build -t <name:tag> [--no-cache] <context>
  docksmith images
  docksmith rmi <name:tag>
  docksmith run [-e KEY=VALUE] <name:tag> [cmd...]
"""

import sys
import os
import argparse

# Make sure we can import sibling modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store import Store
from builder import Builder
from runtime import Runtime


def cmd_build(args):
    if ":" not in args.tag:
        print(f"Error: tag must be in name:tag format, got '{args.tag}'")
        sys.exit(1)

    name, tag = args.tag.split(":", 1)
    context = os.path.abspath(args.context)

    if not os.path.isdir(context):
        print(f"Error: context directory '{context}' does not exist")
        sys.exit(1)

    docksmithfile = os.path.join(context, "Docksmithfile")
    if not os.path.isfile(docksmithfile):
        print(f"Error: no Docksmithfile found in '{context}'")
        sys.exit(1)

    store = Store()
    builder = Builder(store, context, no_cache=args.no_cache)
    builder.build(docksmithfile, name, tag)


def cmd_images(args):
    store = Store()
    images = store.list_images()

    if not images:
        print("No images found.")
        return

    # Print table
    fmt = "{:<20} {:<10} {:<15} {:<25}"
    print(fmt.format("NAME", "TAG", "ID", "CREATED"))
    print("-" * 72)
    for img in images:
        digest = img.get("digest", "")
        short_id = digest.replace("sha256:", "")[:12] if digest else "unknown"
        print(fmt.format(
            img.get("name", ""),
            img.get("tag", ""),
            short_id,
            img.get("created", "")
        ))


def cmd_rmi(args):
    if ":" not in args.name_tag:
        print(f"Error: specify image as name:tag, got '{args.name_tag}'")
        sys.exit(1)

    name, tag = args.name_tag.split(":", 1)
    store = Store()
    store.remove_image(name, tag)


def cmd_run(args):
    if ":" not in args.name_tag:
        print(f"Error: specify image as name:tag, got '{args.name_tag}'")
        sys.exit(1)

    name, tag = args.name_tag.split(":", 1)

    # Parse -e KEY=VALUE overrides
    env_overrides = {}
    for e in args.env:
        if "=" not in e:
            print(f"Error: -e value must be KEY=VALUE, got '{e}'")
            sys.exit(1)
        k, v = e.split("=", 1)
        env_overrides[k] = v

    store = Store()
    manifest = store.get_image(name, tag)
    if manifest is None:
        print(f"Error: image '{name}:{tag}' not found")
        sys.exit(1)

    runtime = Runtime(store)
    runtime.run(manifest, cmd_override=args.cmd if args.cmd else None, env_overrides=env_overrides)


def main():
    parser = argparse.ArgumentParser(
        prog="docksmith",
        description="A simplified Docker-like build and runtime system"
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- build ---
    build_parser = subparsers.add_parser("build", help="Build an image from a Docksmithfile")
    build_parser.add_argument("-t", dest="tag", required=True, metavar="name:tag", help="Name and tag for the image")
    build_parser.add_argument("--no-cache", action="store_true", help="Disable build cache")
    build_parser.add_argument("context", help="Path to the build context directory")

    # --- images ---
    subparsers.add_parser("images", help="List all images")

    # --- rmi ---
    rmi_parser = subparsers.add_parser("rmi", help="Remove an image")
    rmi_parser.add_argument("name_tag", metavar="name:tag")

    # --- run ---
    run_parser = subparsers.add_parser("run", help="Run a container")
    run_parser.add_argument("-e", dest="env", action="append", default=[], metavar="KEY=VALUE",
                            help="Set environment variable (repeatable)")
    run_parser.add_argument("name_tag", metavar="name:tag")
    run_parser.add_argument("cmd", nargs=argparse.REMAINDER, help="Override CMD")

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    if args.command == "build":
        cmd_build(args)
    elif args.command == "images":
        cmd_images(args)
    elif args.command == "rmi":
        cmd_rmi(args)
    elif args.command == "run":
        cmd_run(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
