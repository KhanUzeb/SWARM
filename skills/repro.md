When to use: a bug report needs a pack @coder can actually run.

Inputs: the report after the command, plus any files already in the workspace.

Steps:
1. Restate expected vs actual in one line each.
2. Exact steps, environment notes, and a minimal test or script.
3. If it is a web bug, browser_snapshot or firecrawl_scrape the page; do not invent UI.
4. Write `repro/<slug>.md` (and code via write_workspace when a script helps).
5. @coder to implement the fix if they are in the room.

Output: repro pack path + the minimal steps in chat.

Approval: none to write the pack. request_approval before filing on an external tracker.
