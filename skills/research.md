When to use: you need current web facts, competitors, docs, or citations.

Inputs: the question after the command (`/research latest Groq deprecations`).

Steps:
1. Prefer tavily_search or exa_search. If both keys are missing, say so and use fetch_url only on links the human already gave.
2. Open 1–2 strongest URLs with firecrawl_scrape (or fetch_url).
3. Separate facts from guesses. Keep source URLs.
4. Write `research/<slug>.md` to the workspace when the answer is more than a few bullets.

Output: findings, sources, what is still unknown.

Approval: none for reading the public web. request_approval before sending outreach or posting anywhere.
