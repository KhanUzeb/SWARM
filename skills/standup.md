When to use: start of day, or when someone asks "what's blocking us?"

Inputs: this channel's recent history. Optional focus after the command (`/standup auth`).

Steps:
1. Read recent messages with search_channel_history and channel_digest.
2. Group into: shipped, in progress, blockers, asks.
3. Name an owner when the thread already has one.

Output: short bullets, newest work first. One line if the channel is quiet.

Approval: none. Do not ping people outside this workspace.
