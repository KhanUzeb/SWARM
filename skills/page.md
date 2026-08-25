When to use: turn a public URL into readable notes in the shared computer.

Inputs: a URL after the command (`/page https://example.com/docs`).

Steps:
1. firecrawl_scrape the URL. If Firecrawl is not connected, fetch_url and say the scrape is crude.
2. Write `pages/<host>-<slug>.md` with title, URL, and markdown body (trim noise).
3. Add 3–5 takeaways at the top.

Output: workspace path plus the takeaways in chat.

Approval: none for public pages. Do not log in or scrape behind auth. request_approval before paying walls or credentialed apps.
