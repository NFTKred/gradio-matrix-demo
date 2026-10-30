"""Set up the Matrix side of the demo with the matrix_kred SDK.

Creates the demo node, registers the host, planner and critic agents, sets the
node's thread costs, and funds the host with an exact allowance. Every value it
creates is written to .env as soon as it exists (agent tokens are returned only
once), and a rerun skips anything already recorded there.

Reads your own user key from MATRIX_OWNER_TOKEN (in .env or the environment).
Without --apply it only prints what it would do.

    python scripts/setup_demo.py --domain example.kred --budget 50
    python scripts/setup_demo.py --domain example.kred --budget 50 --apply
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

from dotenv import dotenv_values, set_key
from matrix_kred import MatrixClient, models

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
THREAD_COST = 1

# (role, display name, .env key for the agent's ID, .env key for its token)
AGENTS = [
    ("host", "Demo Host", "MATRIX_HOST_ID", "MATRIX_HOST_TOKEN"),
    ("planner", "Planner", "MATRIX_PLANNER_ID", "MATRIX_PLANNER_TOKEN"),
    ("critic", "Critic", "MATRIX_CRITIC_ID", "MATRIX_CRITIC_TOKEN"),
]


def idempotency_key(action: str, **fields: str) -> str:
    """Same request -> same key (safe retry); any changed field -> a new key."""
    body = "&".join(f"{k}={v}" for k, v in sorted(fields.items()))
    return f"setup-demo:{action}:{hashlib.sha256(body.encode()).hexdigest()[:24]}"


def record(env_file: Path, key: str, value: str) -> None:
    env_file.touch(exist_ok=True)
    set_key(str(env_file), key, value, quote_mode="never")


def create_node(client: MatrixClient, args, state: dict, env_file: Path) -> str:
    if state.get("MATRIX_DEMO_NODE_ID"):
        print(f"node: already recorded {state['MATRIX_DEMO_NODE_ID']}")
        return state["MATRIX_DEMO_NODE_ID"]
    path = args.path or args.node_name
    print(f"node: nodes.create(domain={args.domain!r}, path={path!r}, name={args.node_name!r}, visibility='public')")
    if not args.apply:
        return "<new node>"
    node = client.nodes.create(
        domain=args.domain,
        path=path,
        name=args.node_name,
        visibility="public",
        idempotency_key=idempotency_key(
            "node", domain=args.domain, path=path, name=args.node_name, visibility="public"
        ),
    )
    record(env_file, "MATRIX_DEMO_NODE_ID", node.id)
    print(f"node: created {node.id} (path={node.path}, owner={node.owner})")
    return node.id


def register_agents(client: MatrixClient, args, state: dict, env_file: Path) -> dict[str, str]:
    ids: dict[str, str] = {}
    for role, name, id_key, token_key in AGENTS:
        if state.get(id_key) and state.get(token_key):
            ids[role] = state[id_key]
            print(f"{role}: already recorded {ids[role]}")
            continue
        address = f"{args.address_prefix}-{role}"
        print(f"{role}: agents.create(name={name!r}, address={address!r})")
        if not args.apply:
            ids[role] = f"<new {role}>"
            continue
        agent = client.agents.create(name=name, address=address, idempotency_key=idempotency_key("agent", name=name, address=address))
        # The token is shown once: save it before doing anything else.
        record(env_file, token_key, agent.token)
        record(env_file, id_key, agent.id)
        ids[role] = agent.id
        print(f"{role}: registered {agent.id} (token saved to {env_file.name} as {token_key})")
    return ids


def set_costs(client: MatrixClient, args, node_id: str) -> None:
    print(f"node: nodes.update_governance({node_id}, costs=thread={THREAD_COST}, thread_participants={THREAD_COST})")
    if not args.apply:
        return
    client.nodes.update_governance(
        node_id, costs=models.NodeCostsBody(thread=THREAD_COST, thread_participants=THREAD_COST)
    )
    costs = client.nodes.get(node_id).governance.costs
    print(f"node: effective costs now {costs}")


def fund_host(client: MatrixClient, args, host_id: str) -> None:
    print(f"host: allowances.set({host_id}, amount_decimal={args.budget!r})")
    if not args.apply:
        return
    client.allowances.set(
        host_id,
        amount_decimal=args.budget,
        idempotency_key=idempotency_key("allowance", child=host_id, amount=args.budget),
    )
    print(f"host: allowance now {client.allowances.get(host_id).model_dump(mode='json')}")


def main(argv: list[str] | None = None, env_file: Path = ENV_FILE) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", required=True, help="a domain (channel) you own, to create the node under")
    parser.add_argument("--node-name", default="matrix-agents-demo")
    # Without a path the API targets the domain's root node, which already exists (409).
    parser.add_argument("--path", help="node path under the domain (default: the node name)")
    parser.add_argument("--address-prefix", default="matrix-agents-demo", help="agent addresses are <prefix>-host etc.")
    parser.add_argument("--budget", required=True, help="host allowance in XP; each run costs 1")
    parser.add_argument("--apply", action="store_true", help="make the calls (default: print the plan only)")
    args = parser.parse_args(argv)

    state = {k: v for k, v in dotenv_values(env_file).items() if v} if env_file.exists() else {}
    token = os.environ.get("MATRIX_OWNER_TOKEN") or state.get("MATRIX_OWNER_TOKEN")
    if not token:
        raise SystemExit(f"Set MATRIX_OWNER_TOKEN in {env_file} or the environment.")
    if not args.apply:
        print("Dry run: nothing is created. Add --apply to make these calls.\n")

    with MatrixClient(token) as client:
        node_id = create_node(client, args, state, env_file)
        ids = register_agents(client, args, state, env_file)
        set_costs(client, args, node_id)
        fund_host(client, args, ids["host"])

    if args.apply and not state.get("REPLY_CAP"):
        record(env_file, "REPLY_CAP", "4")
    print(
        "\nNext: set MATRIX_CAT_NODE_ID in .env, then point each agent's webhook at its Vercel URL "
        "(PUT /agents/{id}/webhook) once the agents are deployed."
    )


if __name__ == "__main__":
    main()
