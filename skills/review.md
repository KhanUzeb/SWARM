When to use: review code, a workspace file, or the last program @coder posted.

Inputs: a path, a paste, or "the latest listing in this channel".

Steps:
1. Read the file with read_workspace (or the fenced block in history). Do not invent code.
2. Check correctness, tests, secrets, and anything that would break production.
3. Structure as: what is good, what is wrong, what to change first.
4. For multi-file work, list_workspace first.

Output: review notes. Optionally write `reviews/<slug>.md`.

Approval: none for a review. request_approval before merging, deploying, or deleting.
