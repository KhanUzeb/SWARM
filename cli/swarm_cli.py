#!/usr/bin/env python3
"""
swarm-cli — JSON in, JSON out. Built for scripts and agents.

Usage:
    swarm_cli.py register <handle>
    swarm_cli.py channels
    swarm_cli.py history <channel> [--limit N]
    swarm_cli.py post <channel> <author> <body> [--parent ID]
    swarm_cli.py post <channel> <author> --stdin
    swarm_cli.py react <message_id> <author> <emoji>
    swarm_cli.py agents [--channel ID]

Env:
    SWARM_URL     base URL of the relay (default http://localhost:8000)
    SWARM_TOKEN   composite auth token, "<handle>:<raw>", from `register`
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("SWARM_URL", "http://localhost:8000")


def _request(method: str, path: str, payload: dict | None = None, auth: bool = False) -> dict | None:
    url = f"{BASE_URL}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if auth:
        token = os.environ.get("SWARM_TOKEN")
        if not token:
            print(json.dumps({"error": "SWARM_TOKEN not set; run `register` first"}), file=sys.stderr)
            sys.exit(1)
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        print(json.dumps({"error": e.code, "detail": e.read().decode()}), file=sys.stderr)
        sys.exit(1)


def cmd_register(args) -> None:
    result = _request("POST", "/api/register", {"handle": args.handle})
    print(json.dumps(result, indent=2))
    print(f"\nexport SWARM_TOKEN='{result['token']}'", file=sys.stderr)


def cmd_channels(_args) -> None:
    print(json.dumps(_request("GET", "/api/channels"), indent=2))


def cmd_history(args) -> None:
    print(json.dumps(
        _request("GET", f"/api/channels/{args.channel}/messages?limit={args.limit}"), indent=2,
    ))


def cmd_post(args) -> None:
    body = sys.stdin.read().strip() if args.stdin else args.body
    if not body:
        print(json.dumps({"error": "empty body"}), file=sys.stderr)
        sys.exit(1)
    payload = {"author": args.author, "body": body, "author_kind": args.kind}
    if args.parent:
        payload["parent_id"] = args.parent
    print(json.dumps(
        _request("POST", f"/api/channels/{args.channel}/messages", payload, auth=True), indent=2,
    ))


def cmd_react(args) -> None:
    _request(
        "POST", f"/api/messages/{args.message_id}/reactions",
        {"author": args.author, "emoji": args.emoji}, auth=True,
    )
    print(json.dumps({"ok": True}))


def cmd_agents(args) -> None:
    path = "/api/agents"
    if args.channel:
        path += f"?channel_id={args.channel}"
    print(json.dumps(_request("GET", path), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="swarm-cli", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_reg = sub.add_parser("register")
    p_reg.add_argument("handle")
    p_reg.set_defaults(func=cmd_register)

    sub.add_parser("channels").set_defaults(func=cmd_channels)

    p_hist = sub.add_parser("history")
    p_hist.add_argument("channel")
    p_hist.add_argument("--limit", type=int, default=50)
    p_hist.set_defaults(func=cmd_history)

    p_post = sub.add_parser("post")
    p_post.add_argument("channel")
    p_post.add_argument("author")
    p_post.add_argument("body", nargs="?", default="")
    p_post.add_argument("--stdin", action="store_true")
    p_post.add_argument("--kind", default="human", choices=["human", "agent", "system"])
    p_post.add_argument("--parent", type=int, default=None)
    p_post.set_defaults(func=cmd_post)

    p_react = sub.add_parser("react")
    p_react.add_argument("message_id", type=int)
    p_react.add_argument("author")
    p_react.add_argument("emoji")
    p_react.set_defaults(func=cmd_react)

    p_agents = sub.add_parser("agents")
    p_agents.add_argument("--channel", default=None)
    p_agents.set_defaults(func=cmd_agents)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
